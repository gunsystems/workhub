from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase,RequestFactory
from .help_content import page_help


class PageHelpTests(TestCase):
    def test_guidance_changes_with_page_and_includes_workflow_rules(self):
        factory=RequestFactory()
        for name,expected in [('projects','진행률'),('personal_card_edit','일정 기준'),('google_export_task','종일 일정'),('administrators','권한이 같지')]:
            request=factory.get('/');request.resolver_match=SimpleNamespace(url_name=name,kwargs={})
            self.assertIn(expected,str(page_help(request)))
            self.assertTrue(page_help(request)['page_help']['flow_nodes'][-1]['final'])
        request=factory.get('/tasks/1/actions/progress/');request.resolver_match=SimpleNamespace(url_name='task_action',kwargs={'action':'progress'})
        self.assertEqual(page_help(request)['page_help']['title'],'진행 보고')

    def test_login_and_staff_admin_include_help_without_authentication_changes(self):
        response=self.client.get('/login/')
        self.assertContains(response,'data-page-help-open')
        self.assertContains(response,'role="img"')
        self.assertContains(response,'OTP 코드는')
        admin=get_user_model().objects.create_superuser(username='help-admin',password='test-only-123')
        self.client.force_login(admin)
        response=self.client.get('/admin/tasks/employee/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-page-help-open')
        self.assertContains(response,'직원 관리')


class HelpAudienceTests(TestCase):
    def setUp(self):
        from .models import Department, Employee
        self.factory = RequestFactory()
        self.staff = get_user_model().objects.create_user(username='help-member')
        self.admin = get_user_model().objects.create_superuser(username='help-owner', password='test-only-123')
        self.executive = get_user_model().objects.create_user(username='help-executive')
        self.leader = get_user_model().objects.create_user(username='help-leader')
        department = Department.objects.create(name='Help team')
        for user, position in [(self.staff, 'member'), (self.executive, 'executive'), (self.leader, 'leader')]:
            Employee.objects.create(user=user, name=user.username, department=department, position=position)

    def help(self, name, user):
        request = self.factory.get('/')
        request.user = user
        request.resolver_match = SimpleNamespace(url_name=name, kwargs={})
        return page_help(request)['page_help']

    def test_document_operations_are_scoped_and_requests_do_not_mutate_topics(self):
        from .help_content import TOPICS
        from copy import deepcopy
        original = deepcopy(TOPICS['shared_documents'])
        for name in ['shared_documents', 'shared_document_link', 'shared_folder_manage', 'shared_document_access']:
            staff = self.help(name, self.staff)
            self.assertNotIn('management_notes', staff)
            self.assertNotIn('휴지통', str(staff))
            self.assertNotIn('액세스 관리', str(staff))
            self.assertIn('관리자에게 문의', str(staff))
            self.assertIn('빈 폴더', str(self.help(name, self.executive)['management_notes']))
            self.assertNotIn('액세스 관리', str(self.help(name, self.executive)))
            self.assertIn('액세스 관리', str(self.help(name, self.admin)['management_notes']))
            self.assertEqual(staff, self.help(name, self.staff))
        self.assertEqual(original, TOPICS['shared_documents'])

    def test_employee_help_keeps_actions_without_admin_setup(self):
        self.assertIn('응답 제출', str(self.help('survey_detail', self.staff)))
        self.assertNotIn('독려', str(self.help('survey_detail', self.staff)))
        self.assertIn('독려', str(self.help('survey_detail', self.admin)))
        for name in ['notice_list', 'notice_detail']:
            self.assertNotIn('예약', str(self.help(name, self.staff)))
            self.assertIn('발송', str(self.help(name, self.admin)))
        for name in ['offboarding_assignments', 'offboarding_item']:
            guidance = str(self.help(name, self.staff))
            self.assertIn('증빙', guidance)
            self.assertIn('취소 확인', guidance)
            self.assertNotIn('퇴사 직원을 등록', guidance)
        self.assertNotIn('셀프 결재', str(self.help('leave_detail', self.staff)))
        self.assertNotIn('개발·테스트', str(self.help('approval_portal', self.admin)))
        self.assertNotIn('보류 중', str(self.help('period_reports', self.leader)))
        self.assertNotIn('제출 현황', str(self.help('period_reports', self.staff)))
        self.assertIn('제출 현황', str(self.help('period_reports', self.leader)))

    def test_rendered_employee_dialog_and_guide_exclude_operator_sections(self):
        from django.template.loader import render_to_string
        request = self.factory.get('/guide/')
        request.user = self.staff
        html = render_to_string('tasks/page_help.html', {'request': request, 'page_help': self.help('shared_documents', self.staff)})
        self.assertNotIn('문서 관리 담당자 안내', html)
        self.assertNotIn('액세스 관리', html)
        self.client.force_login(self.staff)
        response = self.client.get('/guide/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '직원 초대와 자동 연결 · 관리자')
        self.assertNotContains(response, '사용 · 테스트 가이드')
        self.assertContains(response, '하이웍스 앱의 알림')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/guide/'), '직원 초대와 자동 연결 · 관리자')
