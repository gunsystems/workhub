from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.utils import timezone
from accounts.models import AccessProfile
from .models import Department,Employee,Survey,SurveyQuestion,SurveyParticipation,SurveySubmission,SurveyDelivery,StaffAssessment
from .surveys import publish,submit,queue_notifications,deliver_notifications
from .assessments import AssessmentForm,save_assessment


@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PeopleTests(TestCase):
    def setUp(self):
        U=get_user_model();self.admin=U.objects.create_superuser('operator')
        self.team=Department.objects.create(name='팀')
        self.users=[];self.employees=[]
        for name,pos in [('boss','executive'),('worker','member'),('other','member')]:
            user=U.objects.create_user(name,email=name+'@gunsystems.co.kr')
            AccessProfile.objects.create(user=user,email=user.email,verified_at=timezone.now(),require_mfa=False)
            self.users.append(user);self.employees.append(Employee.objects.create(user=user,name=name,position=pos,department=self.team))
        self.boss,self.worker,self.other=self.users
    def survey(self,anonymous=False):
        s=Survey.objects.create(title='워크숍',creator=self.admin,closes_at=timezone.now()+timedelta(days=7),anonymous=anonymous,audience='person')
        s.employees.add(self.employees[1])
        q=SurveyQuestion.objects.create(survey=s,label='희망 장소',kind='single',options='서울\n부산')
        return s,q
    def test_publish_freezes_audience_and_forbids_nonadmin(self):
        s,q=self.survey()
        with self.assertRaises(PermissionDenied):publish(s.pk,self.worker)
        publish(s.pk,self.admin);s.employees.add(self.employees[2])
        self.assertEqual(s.participations.count(),1)
        with self.assertRaises(ValidationError):publish(s.pk,self.admin)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/work/surveys/{s.pk}/').status_code,403)
    def test_draft_validation_submit_and_no_duplicate(self):
        s,q=self.survey();publish(s.pk,self.admin)
        form=submit(s.pk,self.worker,{str(q.pk):'도쿄'})
        self.assertIsNotNone(form);self.assertFalse(SurveySubmission.objects.exists())
        self.assertIsNone(submit(s.pk,self.worker,{},draft=True))
        self.assertFalse(s.participations.get().submitted)
        self.assertIsNone(submit(s.pk,self.worker,{str(q.pk):'서울'}))
        self.assertEqual(SurveySubmission.objects.get().employee,self.employees[1])
        with self.assertRaises(ValidationError):submit(s.pk,self.worker,{str(q.pk):'부산'})
    def test_anonymous_answers_have_no_employee_or_draft_mapping(self):
        s,q=self.survey(True);publish(s.pk,self.admin)
        submit(s.pk,self.worker,{str(q.pk):'서울'},draft=True)
        submit(s.pk,self.worker,{str(q.pk):'서울'})
        self.assertIsNone(SurveySubmission.objects.get().employee_id)
        self.assertEqual(s.participations.get().draft,{})
        self.client.force_login(self.admin)
        response=self.client.get(f'/work/surveys/{s.pk}/results/')
        self.assertContains(response,'서울');self.assertNotContains(response,self.worker.email)
        self.assertEqual(response.context['named'],[])
    def test_deadline_and_start_are_enforced_on_server(self):
        s,q=self.survey();publish(s.pk,self.admin)
        s.closes_at=timezone.now()-timedelta(seconds=1);s.save()
        with self.assertRaises(ValidationError):submit(s.pk,self.worker,{str(q.pk):'서울'})
        queue_notifications();self.assertFalse(SurveyDelivery.objects.exists())
        s.closes_at=timezone.now()+timedelta(days=9);s.starts_at=timezone.now()+timedelta(days=1);s.save()
        with self.assertRaises(ValidationError):submit(s.pk,self.worker,{str(q.pk):'서울'})
    @patch('accounts.services.deliver')
    def test_reminders_deduplicate_and_recheck_submission(self,send):
        s,q=self.survey();publish(s.pk,self.admin)
        queue_notifications();queue_notifications();self.assertEqual(SurveyDelivery.objects.count(),1)
        submit(s.pk,self.worker,{str(q.pk):'서울'})
        deliver_notifications();send.assert_not_called()
        self.assertEqual(SurveyDelivery.objects.get().state,'skipped')
    @patch('accounts.services.deliver',side_effect=RuntimeError('timeout'))
    def test_ambiguous_delivery_never_replays(self,send):
        s,q=self.survey();publish(s.pk,self.admin);queue_notifications()
        deliver_notifications();deliver_notifications()
        self.assertEqual(send.call_count,1);self.assertEqual(SurveyDelivery.objects.get().state,'unknown')
    def test_results_and_authoring_are_admin_only(self):
        s,q=self.survey();publish(s.pk,self.admin)
        self.client.force_login(self.worker)
        for url in ['/work/surveys/new/',f'/work/surveys/{s.pk}/results/',f'/work/surveys/{s.pk}/edit/']:
            self.assertEqual(self.client.get(url).status_code,403 if 'results' in url or 'new' in url else 403)
        self.client.force_login(self.admin)
        for url in ['/work/surveys/','/work/surveys/new/',f'/work/surveys/{s.pk}/',f'/work/surveys/{s.pk}/results/']:
            self.assertEqual(self.client.get(url).status_code,200)
    def assessment_data(self,**kw):
        today=timezone.localdate()
        return {'employee':str(self.employees[1].pk),'task':'','period_start':str(today.replace(day=1)),
                'period_end':str(today),'quality':'4','contribution':'3','ability':'','attitude':'5','collaboration':'4',
                'evidence':'고객 장애 원인을 분석하고 재발 방지 문서를 작성했습니다.','context':'긴급 지원 병행','reason':'관찰 근거 기록','expected_revision':'0',**kw}
    def test_only_business_executives_can_access_even_root_is_denied(self):
        for user in [self.admin,self.worker]:
            self.client.force_login(user)
            for url in ['/work/assessments/','/work/assessments/new/']:
                self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.boss)
        for url in ['/work/assessments/','/work/assessments/new/']:
            self.assertEqual(self.client.get(url).status_code,200)
    def test_assessment_exclusions_revision_history_and_stale_update(self):
        form=AssessmentForm(self.assessment_data(),user=self.boss)
        self.assertTrue(form.is_valid(),form.errors)
        obj=save_assessment(form,self.boss,final=True)
        self.assertNotIn('ability',obj.scores)
        changed=AssessmentForm(self.assessment_data(expected_revision='1',quality='5',reason='추가 결과물 확인'),instance=obj,user=self.boss)
        self.assertTrue(changed.is_valid(),changed.errors);save_assessment(changed,self.boss,final=True)
        self.assertEqual(obj.history.count(),2)
        self.assertEqual(obj.history.order_by('revision').first().snapshot['scores']['quality'],4)
        stale=AssessmentForm(self.assessment_data(expected_revision='1'),instance=obj,user=self.boss)
        self.assertTrue(stale.is_valid(),stale.errors)
        with self.assertRaises(ValidationError):save_assessment(stale,self.boss,final=True)
    def test_self_assessment_and_forged_scores_rejected(self):
        for data in [self.assessment_data(employee=str(self.employees[0].pk)),self.assessment_data(quality='6')]:
            self.assertFalse(AssessmentForm(data,user=self.boss).is_valid())

    def test_survey_authoring_formset_and_response_pages(self):
        self.client.force_login(self.admin)
        now=timezone.localtime()
        data={'title':'워크숍 의견','description':'희망 프로그램을 알려주세요.','audience':'person',
              'employees':[str(self.employees[1].pk)],'starts_at':(now-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'),
              'closes_at':(now+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),'questions-TOTAL_FORMS':'1',
              'questions-INITIAL_FORMS':'0','questions-MIN_NUM_FORMS':'1','questions-MAX_NUM_FORMS':'20',
              'questions-0-label':'희망 프로그램','questions-0-kind':'multiple','questions-0-options':'교육\n체험','questions-0-required':'on'}
        response=self.client.post('/work/surveys/new/',data)
        self.assertEqual(response.status_code,302)
        s=Survey.objects.get();q=s.questions.get();publish(s.pk,self.admin)
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(f'/work/surveys/{s.pk}/'),'희망 프로그램')
        response=self.client.post(f'/work/surveys/{s.pk}/',{'action':'submit',str(q.pk):['교육','체험']})
        self.assertEqual(response.status_code,302)
        self.assertEqual(s.submissions.get().answers[str(q.pk)],['교육','체험'])
        self.assertContains(self.client.get(f'/work/surveys/{s.pk}/'),'제출을 완료했습니다')

    def test_assessment_routes_and_invalid_filter(self):
        self.client.force_login(self.boss)
        response=self.client.post('/work/assessments/new/',{**self.assessment_data(),'action':'final'})
        self.assertEqual(response.status_code,302)
        obj=StaffAssessment.objects.get()
        self.assertContains(self.client.get(response['Location']),'고객 장애 원인')
        self.assertContains(self.client.get('/work/assessments/?year=bad&period=year&month=99'),'수행 평가')
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(response['Location']).status_code,403)
        self.assertEqual(self.client.get(f'/work/assessments/{obj.pk}/edit/').status_code,403)

    def test_reminder_stages_no_burst_and_no_post_deadline(self):
        s,q=self.survey();s.closes_at=timezone.now()+timedelta(days=2);s.save();publish(s.pk,self.admin)
        queue_notifications();self.assertEqual(list(SurveyDelivery.objects.values_list('stage',flat=True)),['initial'])
        job=SurveyDelivery.objects.get();job.state='sent';job.attempted_at=timezone.now();job.save()
        queue_notifications();self.assertEqual(SurveyDelivery.objects.count(),1)
        job.attempted_at=timezone.now()-timedelta(days=1,minutes=1);job.save()
        queue_notifications();queue_notifications();self.assertEqual(SurveyDelivery.objects.count(),2)
        self.assertTrue(SurveyDelivery.objects.filter(stage='day3').exists())
        queue_notifications(now=s.closes_at+timedelta(seconds=1));self.assertEqual(SurveyDelivery.objects.count(),2)
