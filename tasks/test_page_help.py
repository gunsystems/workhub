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
