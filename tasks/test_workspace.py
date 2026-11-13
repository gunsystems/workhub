from datetime import date
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,Client
from django.utils import timezone
from .models import (Department,Employee,Task,TaskParticipation,ApprovalStep,TaskEvent,
                     PersonalBoard,PersonalColumn,PersonalCard,WorkNotification)
from .collaboration import change,OfficialTaskForm
from .permissions import can_review,visible_tasks,can_edit
from .workflow import transition
from .personal import next_date


class WorkspaceTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.ceo=U.objects.create_superuser('ceo',password='test')
        self.author=U.objects.create_user('author',password='test')
        self.worker=U.objects.create_user('worker',password='test')
        self.helper=U.objects.create_user('helper',password='test')
        self.leader=U.objects.create_user('leader',password='test')
        self.head=U.objects.create_user('head',password='test')
        self.other=U.objects.create_user('other',password='test')
        self.team=Department.objects.create(name='개발팀')
        self.emp={}
        for u in [self.author,self.worker,self.helper,self.leader,self.head,self.other]:
            self.emp[u.pk]=Employee.objects.create(user=u,name=u.username,department=self.team,
                role='manager' if u==self.leader else 'employee', position='leader' if u==self.leader else 'member')
        self.today=timezone.localdate()
        self.client.force_login(self.author)

    def task(self,reviewers=None,helpers=False,status='doing'):
        obj=Task.objects.create(title='공식 업무',assignee=self.emp[self.worker.pk],creator=self.author,
            workflow_version=2,start_date=self.today,due_date=self.today,status=status)
        TaskParticipation.objects.create(task=obj,employee=obj.assignee)
        if helpers: TaskParticipation.objects.create(task=obj,employee=self.emp[self.helper.pk])
        for n,u in enumerate(reviewers if reviewers is not None else [self.leader,self.head],1):
            ApprovalStep.objects.create(task=obj,approver=u,approver_name=u.username,position=n,stage=n)
        return obj

    def act(self,user,task,kind,**data):
        task.refresh_from_db()
        participant=task.assignments.filter(employee__user=user).first()
        if kind in ['progress','finish','submit'] and task.status in ['todo','doing','blocked'] and participant and not participant.accepted_at:
            change(user,task.pk,'accept',{'revision':task.revision,'body':'업무 내용과 기한 확인'})
            task.refresh_from_db()
        return change(user,task.pk,kind,{'revision':task.revision,'body':'기록된 처리 사유',**data})

    def form_data(self,**extra):
        return {'title':'새 공식 업무','assignee':self.emp[self.worker.pk].pk,'start_date':self.today,
            'due_date':self.today,'status':'todo','importance':'normal','priority':3,
            'allocation':'shared','revision':0,'approver_1':self.leader.pk,**extra}

    def test_sequential_approval_finishes_at_head_without_ceo(self):
        task=self.task()
        self.act(self.worker,task,'submit')
        with self.assertRaises(PermissionDenied): self.act(self.head,task,'review',decision='approve')
        self.act(self.leader,task,'review',decision='approve')
        task.refresh_from_db(); self.assertEqual(task.status,'review')
        self.act(self.head,task,'review',decision='approve')
        task.refresh_from_db(); self.assertEqual(task.status,'done')
        self.assertFalse(task.approval_steps.filter(approver=self.ceo).exists())
        self.assertIsNotNone(task.approved_at)

    def test_ceo_monitoring_feedback_is_not_an_extra_approval_step(self):
        task=self.task([self.leader])
        self.assertIn(task,visible_tasks(self.ceo))
        self.act(self.ceo,task,'comment')
        self.assertEqual(task.approval_steps.count(),1)
        self.act(self.worker,task,'submit')
        self.assertFalse(can_review(self.ceo,Task.objects.get(pk=task.pk)))
        self.act(self.leader,task,'review',decision='approve')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'done')

    def test_explicit_ceo_override_audited_and_other_users_cannot_override(self):
        task=self.task()
        self.act(self.worker,task,'submit')
        with self.assertRaises(PermissionDenied): self.act(self.leader,task,'override',decision='approve')
        self.act(self.ceo,task,'override',decision='approve')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'done')
        event=task.events.get(kind='override')
        self.assertEqual(event.snapshot['decision'],'approve')
        self.assertTrue(task.approval_steps.filter(decision='pending').exists())
        self.act(self.ceo,task,'override',decision='reopen')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'doing')

    def test_return_recall_and_resubmit_preserve_report_and_repeat_approvals(self):
        task=self.task()
        self.act(self.worker,task,'submit')
        self.act(self.leader,task,'review',decision='approve')
        self.act(self.head,task,'review',decision='return')
        self.act(self.worker,task,'submit')
        self.assertEqual(task.events.filter(kind='submitted').count(),2)
        self.assertEqual(Task.objects.get(pk=task.pk).report_round,2)
        self.assertTrue(can_review(self.leader,Task.objects.get(pk=task.pk)))
        self.act(self.worker,task,'recall')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'doing')

    def test_legacy_approval_endpoint_cannot_bypass_new_line(self):
        task=self.task()
        self.act(self.worker,task,'submit')
        with self.assertRaises(ValidationError): transition(self.leader,task.pk,'review',{'decision':'approve','quality_score':100,'body':'우회'})
        self.assertEqual(Task.objects.get(pk=task.pk).status,'review')

    def test_stale_double_approval_rejected(self):
        task=self.task()
        self.act(self.worker,task,'submit'); task.refresh_from_db(); old=task.revision
        self.act(self.leader,task,'review',decision='approve')
        with self.assertRaises(ValidationError): change(self.head,task.pk,'review',{'revision':old,'decision':'approve','body':'중복 처리'})
        self.assertEqual(task.events.filter(kind='approved').count(),1)

    def test_parallel_agreement_requires_all_before_final_stage(self):
        task=self.task([self.leader,self.head,self.ceo])
        task.approval_steps.filter(position=2).update(stage=1)
        task.approval_steps.filter(position=3).update(stage=2)
        self.act(self.worker,task,'submit')
        self.assertTrue(can_review(self.leader,Task.objects.get(pk=task.pk)))
        self.assertTrue(can_review(self.head,Task.objects.get(pk=task.pk)))
        self.act(self.head,task,'review',decision='approve')
        self.assertFalse(can_review(self.ceo,Task.objects.get(pk=task.pk)))
        self.act(self.leader,task,'review',decision='approve')
        self.assertTrue(can_review(self.ceo,Task.objects.get(pk=task.pk)))
        self.act(self.ceo,task,'review',decision='approve',quality_score=90)
        task.refresh_from_db(); self.assertEqual(task.status,'done'); self.assertEqual(task.quality_score,90)

    def test_edit_conflict_and_audit_before_values(self):
        task=self.task([self.leader])
        data=self.form_data(title='수정된 지시',revision=0,description='새 지시 내용')
        response=self.client.post(f'/tasks/{task.pk}/edit/',data)
        self.assertEqual(response.status_code,302)
        event=task.events.get(kind='changed')
        self.assertEqual(event.snapshot['before']['title'],'공식 업무')
        response=self.client.post(f'/tasks/{task.pk}/edit/',data)
        self.assertContains(response,'다른 변경이 반영')
        self.assertEqual(task.events.filter(kind='changed').count(),1)

    def test_joint_report_requires_others_finished_and_only_primary_can_submit(self):
        task=self.task(helpers=True)
        with self.assertRaises(ValidationError): self.act(self.worker,task,'submit')
        with self.assertRaises(PermissionDenied): self.act(self.helper,task,'submit')
        self.act(self.helper,task,'progress',progress=40)
        self.act(self.helper,task,'finish')
        self.act(self.worker,task,'submit')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'review')

    def test_no_approval_task_self_completes_and_instruction_edit_restricted(self):
        task=self.task([])
        self.assertFalse(can_edit(self.worker,task))
        self.assertTrue(can_edit(self.author,task))
        self.act(self.worker,task,'submit')
        self.assertEqual(Task.objects.get(pk=task.pk).status,'done')
        with self.assertRaises(ValidationError): self.act(self.worker,task,'progress',progress=20)

    def test_delegate_and_handover_do_not_allow_self_approval(self):
        task=self.task([self.leader])
        with self.assertRaises(ValidationError):self.act(self.author,task,'handover',replacement=self.helper)
        task.refresh_from_db(); self.assertEqual(task.assignee.user_id,self.worker.pk)
        self.assertTrue(task.assignments.filter(employee=self.emp[self.worker.pk]).exists())
        self.act(self.worker,task,'submit')
        with self.assertRaises(ValidationError): self.act(self.author,task,'delegate',replacement=self.worker)
        self.act(self.leader,task,'delegate',replacement=self.head)
        self.assertFalse(can_review(self.leader,Task.objects.get(pk=task.pk)))
        self.act(self.head,task,'review',decision='approve')

    def test_creator_can_cancel_but_not_delete_published_work(self):
        task=self.task()
        self.act(self.author,task,'cancel')
        task.refresh_from_db(); self.assertEqual(task.status,'cancelled')
        with self.assertRaises(ValidationError): self.act(self.worker,task,'progress',progress=50)
        self.assertTrue(TaskEvent.objects.filter(task=task).exists())

    def test_draft_is_private_to_creator_until_published(self):
        task=self.task(status='draft')
        self.assertNotIn(task,visible_tasks(self.worker))
        self.assertIn(task,visible_tasks(self.author))
        self.assertNotIn(task,visible_tasks(self.ceo))
        self.assertNotIn(task,visible_tasks(self.other))
        for user in [self.worker,self.other,self.ceo]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(f'/tasks/{task.pk}/').status_code,404)
        task.status='doing';task.save()
        self.assertIn(task,visible_tasks(self.other))
        self.assertIn(task,visible_tasks(self.ceo))

    def test_form_validation_and_individual_copies(self):
        bad=OfficialTaskForm(data=self.form_data(approver_1=self.worker.pk),user=self.author)
        self.assertFalse(bad.is_valid())
        bad=OfficialTaskForm(data=self.form_data(approver_2=self.leader.pk),user=self.author)
        self.assertFalse(bad.is_valid())
        response=self.client.post('/work/new/',self.form_data(allocation='individual',collaborators=[self.emp[self.helper.pk].pk]))
        self.assertEqual(response.status_code,302,response.context['form'].errors if response.status_code==200 else '')
        tasks=list(Task.objects.filter(workflow_version=2))
        self.assertEqual(len(tasks),2)
        self.assertEqual(tasks[0].group_key,tasks[1].group_key)
        self.assertEqual(tasks[0].participants.count(),1)
        self.assertEqual(tasks[1].participants.count(),1)
        for task in tasks: self.assertEqual(task.approval_steps.count(),1)

    def test_new_pages_render_and_csrf_blocks_forged_changes(self):
        task=self.task()
        for url in ['/work/','/work/new/',f'/tasks/{task.pk}/','/work/notifications/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        client=Client(enforce_csrf_checks=True); client.force_login(self.worker)
        self.assertEqual(client.post(f'/work/{task.pk}/progress/',{'revision':0,'body':'위조','progress':40}).status_code,403)
        self.assertTrue(WorkNotification.objects.count()==0)

    def test_intake_link_creates_new_work_once_and_keeps_requester_informed(self):
        from .models import WorkRequest
        request=WorkRequest.objects.create(title='접수된 요청',description='요청 내용',department=self.team,
            requested_due_date=self.today,creator=self.other,creator_name='요청자')
        self.client.force_login(self.leader)
        response=self.client.get(f'/work/new/?intake={request.pk}')
        self.assertEqual(response.status_code,200)
        response=self.client.post('/work/new/',self.form_data(intake=request.pk))
        self.assertEqual(response.status_code,302)
        request.refresh_from_db(); self.assertEqual(request.task.workflow_version,2)
        self.assertIn(request.task,visible_tasks(self.other))
        self.client.post('/work/new/',self.form_data(intake=request.pk))
        self.assertEqual(Task.objects.count(),1)


class PersonalBoardTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_user('private-owner',password='test')
        self.other=U.objects.create_user('private-other',password='test')
        self.admin=U.objects.create_superuser('private-admin',password='test')
        self.board=PersonalBoard.objects.create(owner=self.owner,name='개인 비공개')
        self.column=PersonalColumn.objects.create(board=self.board,name='할 일')
        self.card=PersonalCard.objects.create(column=self.column,title='비공개 내용',body='나만 보는 메모',due_date=date(2026,1,31),repeat='monthly')
        self.client.force_login(self.owner)

    def test_owner_only_get_post_search_and_promotion_even_for_superuser(self):
        for user in [self.other,self.admin]:
            self.client.force_login(user)
            for url in [f'/work/personal/boards/{self.board.pk}/',f'/work/personal/cards/{self.card.pk}/',f'/work/personal/cards/{self.card.pk}/promote/']:
                self.assertEqual(self.client.get(url).status_code,404,url)
            self.assertEqual(self.client.post(f'/work/personal/cards/{self.card.pk}/action/',{'action':'trash'}).status_code,404)
            response=self.client.get('/work/personal/')
            self.assertNotContains(response,'비공개 내용')
        self.assertEqual(PersonalCard.objects.get(pk=self.card.pk).state,'active')

    def test_recurrence_month_end_and_double_complete_create_one_followup(self):
        url=f'/work/personal/cards/{self.card.pk}/action/'
        self.client.post(url,{'action':'complete'})
        self.client.post(url,{'action':'complete'})
        self.card.refresh_from_db()
        self.assertEqual(self.card.next_occurrence.due_date,date(2026,2,28))
        self.assertEqual(PersonalCard.objects.count(),2)
        followup=self.card.next_occurrence
        self.client.post(f'/work/personal/cards/{followup.pk}/action/',{'action':'complete'})
        followup.refresh_from_db()
        self.assertEqual(followup.next_occurrence.due_date,date(2026,3,31))
        self.assertEqual(Task.objects.count(),0)
        self.assertEqual(next_date(date(2028,1,31),'monthly'),date(2028,2,29))

    def test_move_rejects_foreign_column_and_trash_restore(self):
        foreign=PersonalBoard.objects.create(owner=self.other,name='타인')
        col=PersonalColumn.objects.create(board=foreign,name='외부 목록')
        url=f'/work/personal/cards/{self.card.pk}/action/'
        self.assertEqual(self.client.post(url,{'action':'move','column':col.pk}).status_code,404)
        self.client.post(url,{'action':'trash'}); self.card.refresh_from_db(); self.assertEqual(self.card.state,'trash')
        self.client.post(url,{'action':'restore'}); self.card.refresh_from_db(); self.assertEqual(self.card.state,'active')

    def test_drag_reorders_and_private_reminders_never_leak(self):
        card=PersonalCard.objects.create(column=self.column,title='다음 카드',position=2)
        self.client.post(f'/work/personal/cards/{card.pk}/action/',{'action':'move','column':self.column.pk,'before':self.card.pk})
        self.assertEqual(list(self.column.cards.values_list('pk',flat=True)),[card.pk,self.card.pk])
        from .alerts import action_alerts
        self.assertTrue(any(a['key']==f'personal:{self.card.pk}' for a in action_alerts(self.owner)))
        self.assertFalse(any(a['key'].startswith('personal:') for a in action_alerts(self.admin)))
        response=self.client.get(f'/work/personal/boards/{self.board.pk}/')
        self.assertIn('no-store',response['Cache-Control'])

    def test_board_create_search_checklist_and_escape_html(self):
        self.assertEqual(self.client.get(f'/work/personal/boards/{self.board.pk}/').status_code,200)
        self.assertContains(self.client.get(f'/work/personal/boards/{self.board.pk}/?q=비공개'),'비공개 내용')
        self.assertNotContains(self.client.get(f'/work/personal/boards/{self.board.pk}/?q=없는검색'),'비공개 내용')
        response=self.client.post('/work/personal/cards/new/',{'title':'<script>alert(1)</script>','column':self.column.pk,'items':'점검\n기록','repeat':'','body':'메모'})
        self.assertEqual(response.status_code,302)
        card=PersonalCard.objects.get(title='<script>alert(1)</script>')
        self.client.post(f'/work/personal/cards/{card.pk}/action/',{'action':'check','index':0})
        card.refresh_from_db(); self.assertTrue(card.checklist[0]['done'])
        response=self.client.get(f'/work/personal/boards/{self.board.pk}/')
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.client.post('/work/personal/boards/action/',{'action':'create','name':'두 번째 보드'})
        board=PersonalBoard.objects.get(name='두 번째 보드')
        self.assertEqual(board.columns.count(),3)

    def test_promote_get_is_private_draft_and_does_not_publish(self):
        response=self.client.get(f'/work/personal/cards/{self.card.pk}/promote/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'공유할 내용만')
        self.assertEqual(Task.objects.count(),0)

    def test_mutations_require_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(client.post(f'/work/personal/cards/{self.card.pk}/action/',{'action':'trash'}).status_code,403)


from django.test import TransactionTestCase
from django.db import connections
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor


class ConcurrentApprovalTests(TransactionTestCase):
    def test_two_simultaneous_clicks_only_record_one_approval(self):
        U=get_user_model()
        worker=U.objects.create_user('concurrent-worker')
        reviewer=U.objects.create_user('concurrent-reviewer')
        department=Department.objects.create(name='동시 처리 검증')
        employee=Employee.objects.create(user=worker,name='수행자',department=department)
        Employee.objects.create(user=reviewer,name='검토자',department=department)
        today=timezone.localdate()
        task=Task.objects.create(title='동시 승인',creator=reviewer,assignee=employee,workflow_version=2,
            status='review',submitted_at=timezone.now(),start_date=today,due_date=today)
        TaskParticipation.objects.create(task=task,employee=employee,completed_at=timezone.now())
        ApprovalStep.objects.create(task=task,approver=reviewer,approver_name='검토자',position=1,stage=1)
        barrier=Barrier(2)
        def click():
            try:
                actor=U.objects.get(pk=reviewer.pk)
                barrier.wait(timeout=10)
                change(actor,task.pk,'review',{'revision':0,'decision':'approve','body':'동시 승인 요청'})
                return 'approved'
            except ValidationError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:click(),range(2)))
        self.assertEqual(sorted(results),['approved','conflict'])
        self.assertEqual(task.events.filter(kind='approved').count(),1)
