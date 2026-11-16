import tempfile
import uuid
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Department, Employee, CertificateRequest, CertificatePolicy, CertificateNotice
from . import certificates as service


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False)
class CertificateTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        field = CertificateRequest._meta.get_field('issued_file')
        old = field.storage
        from .certificate_models import CertificateStorage
        field.storage = CertificateStorage(location=self.temp.name)
        self.addCleanup(setattr, field, 'storage', old)
        self.admin = get_user_model().objects.create_superuser('cert_admin', email='admin@example.test')
        team = Department.objects.create(name='증명서 검증팀')
        self.users = {}
        for role in ['member', 'leader', 'executive', 'issuer', 'replacement']:
            user = get_user_model().objects.create_user('cert_'+role, email=role+'@example.test')
            Employee.objects.create(user=user, name=role, department=team,
                position=role if role in ['leader', 'executive'] else 'member')
            self.users[role] = user

    def submit(self, key=None, user=None):
        return service.submit(user or self.users['member'], key or uuid.uuid4(),
            {'kind':'employment', 'purpose':'금융기관 제출'})

    def act(self, item, action, user=None, **kwargs):
        item.refresh_from_db()
        return service.act(item.pk, user or self.admin, action, item.revision, **kwargs)

    def pdf(self):
        return SimpleUploadedFile('회사서류.pdf', b'%PDF-1.4\n%%EOF', content_type='application/pdf')

    def test_submit_idempotent_and_private_despite_same_team_roles(self):
        key = uuid.uuid4(); item = self.submit(key)
        self.assertEqual(self.submit(key).pk, item.pk)
        self.assertEqual(item.events.count(), 1)
        self.assertEqual(service.pending_for(self.admin).count(), 1)
        self.assertTrue(CertificateNotice.objects.filter(user=self.admin, event__request=item).exists())
        for role in ['leader', 'executive', 'issuer']:
            user = self.users[role]
            self.assertFalse(service.visible(user).filter(pk=item.pk).exists())
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('certificate_detail', args=[item.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('certificate_download', args=[item.pk])).status_code, 404)
        with self.assertRaises(PermissionDenied): self.submit(key, self.users['leader'])

    def test_admin_inbox_and_history_render_and_file_required(self):
        item = self.submit(); self.client.force_login(self.admin)
        response = self.client.get('/work/approvals/?tab=inbox')
        self.assertContains(response, reverse('certificate_detail', args=[item.pk]))
        self.assertContains(self.client.get('/work/approvals/?tab=certificates'), '발급담당자 설정')
        item = self.act(item, 'start')
        self.assertEqual(item.state, 'processing')
        with self.assertRaises(ValidationError): self.act(item, 'issue')
        self.assertEqual(self.client.get(reverse('certificate_download', args=[item.pk])).status_code, 404)
        item = self.act(item, 'issue', upload=self.pdf())
        self.assertEqual(item.state, 'issued')
        self.assertFalse(service.pending_for(self.admin).exists())
        self.assertContains(self.client.get('/work/approvals/?tab=history'), item.number)

    def test_download_authorized_no_public_url_and_revocation(self):
        item = self.act(self.submit(), 'issue', upload=self.pdf())
        with self.assertRaises(ValueError): _ = item.issued_file.url
        self.client.force_login(self.users['member'])
        response = self.client.get(reverse('certificate_download', args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        response.close()
        revision = item.revision
        with self.assertRaises(ValidationError): service.act(item.pk, self.admin, 'issue', revision-1, upload=self.pdf())
        with self.assertRaises(ValidationError): self.act(item, 'revoke')
        self.act(item, 'revoke', body='잘못된 기재 수정 필요')
        self.assertEqual(self.client.get(reverse('certificate_download', args=[item.pk])).status_code, 404)
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.pdf'))), 1)

    def test_policy_future_requests_and_explicit_bulk_reassignment(self):
        original = self.submit()
        service.set_policy(self.admin, self.users['issuer'], 0)
        assigned = self.submit()
        original.refresh_from_db(); self.assertIsNone(original.assignee_id)
        self.assertEqual(assigned.assignee_id, self.users['issuer'].pk)
        self.assertTrue(service.can_handle(self.users['issuer'], assigned))
        with self.assertRaises(PermissionDenied): service.set_policy(self.users['executive'], None, 1)
        with self.assertRaises(ValidationError): service.set_policy(self.admin, None, 0)
        count = service.set_policy(self.admin, self.users['replacement'], 1, True)
        self.assertEqual(count, 2)
        self.assertFalse(service.visible(self.users['issuer']).exists())
        self.assertEqual(service.pending_for(self.users['replacement']).count(), 2)
        with self.assertRaises(PermissionDenied): self.act(assigned, 'start', self.users['issuer'])

    def test_individual_reassignment_and_inactive_fallback(self):
        item = self.act(self.submit(), 'reassign', assignee=self.users['issuer'])
        self.assertTrue(service.visible(self.users['issuer']).filter(pk=item.pk).exists())
        with self.assertRaises(PermissionDenied): self.act(item, 'reassign', self.users['issuer'], assignee=self.users['replacement'])
        user = self.users['issuer']; user.is_active = False; user.save()
        self.assertTrue(service.pending_for(self.admin).filter(pk=item.pk).exists())
        self.assertFalse(service.visible(user).exists())

    def test_staff_post_controls_and_signed_submission(self):
        self.client.force_login(self.users['member'])
        response = self.client.get(reverse('certificate_new'))
        self.assertContains(response, '서류 신청')
        token = response.context['form']['submission'].value()
        payload = {'submission':token, 'kind':'employment', 'language':'ko', 'purpose':'개인 보관'}
        response = self.client.post(reverse('certificate_new'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(reverse('certificate_new'), payload).status_code, 302)
        self.assertEqual(CertificateRequest.objects.count(), 1)
        item = CertificateRequest.objects.get()
        self.assertContains(self.client.get(reverse('certificate_detail', args=[item.pk])), '신청 취소')
        self.assertEqual(self.client.get(reverse('certificate_settings')).status_code, 403)
        with self.assertRaises(PermissionDenied): self.act(item, 'start', self.users['member'])
        self.act(item, 'cancel', self.users['member'])
        with self.assertRaises(PermissionDenied): self.act(item, 'cancel', self.users['member'])
        payload['submission'] = 'forged'
        self.assertContains(self.client.post(reverse('certificate_new'), payload), '신청 화면이 만료')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('certificate_new')).status_code, 403)
        self.assertContains(self.client.get(reverse('certificate_settings')), '발급담당자')

    def test_invalid_files_and_date_validation(self):
        from .certificate_views import RequestForm
        for file in [SimpleUploadedFile('x.txt', b'%PDF-1.4'), SimpleUploadedFile('x.pdf', b'not a pdf')]:
            with self.assertRaises(ValidationError): service.validate_pdf(file)
        form = RequestForm({'submission':'x', 'kind':'other', 'purpose':'용도', 'language':'ko',
            'period_start':'2026-03-02', 'period_end':'2026-03-01'})
        self.assertFalse(form.is_valid())
        self.assertIn('document_name', form.errors); self.assertIn('period_end', form.errors)

    def test_custom_name_enabled_only_for_other_including_forged_post(self):
        from .certificate_views import RequestForm
        self.assertTrue(RequestForm().fields['document_name'].disabled)
        data = {'submission':'x','kind':'employment','language':'ko','purpose':'개인 보관','document_name':'위조한 이름'}
        form = RequestForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['document_name'], '')
        data['kind'] = 'other'
        form = RequestForm(data)
        self.assertFalse(form.fields['document_name'].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['document_name'], '위조한 이름')
        data['document_name'] = ''
        form = RequestForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('document_name', form.errors)

    def test_stale_notices_do_not_leak_after_reassignment(self):
        from accounts.models import AccessProfile
        from django.utils import timezone
        from unittest.mock import patch
        for user in [self.admin, *self.users.values()]:
            AccessProfile.objects.create(user=user, email=user.email, verified_at=timezone.now())
        item = self.submit()
        self.act(item, 'reassign', assignee=self.users['issuer'])
        self.act(item, 'reassign', assignee=self.users['replacement'])
        with patch('accounts.services.deliver') as send:
            service.deliver_pending()
            service.deliver_pending()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[2], self.users['replacement'].email)
            self.assertNotIn(item.purpose, send.call_args.args[1])
            self.assertIn('서류 신청 확인하기', send.call_args.kwargs['html_body'])
        self.assertFalse(CertificateNotice.objects.filter(mail_state='pending').exists())

    def test_admin_final_upload_post_and_owner_result_mail(self):
        from accounts.models import AccessProfile
        from django.utils import timezone
        from unittest.mock import patch
        user = self.users['member']
        AccessProfile.objects.create(user=user, email=user.email, verified_at=timezone.now())
        item = self.submit(); self.client.force_login(self.admin)
        url = reverse('certificate_detail', args=[item.pk])
        response = self.client.post(url, {'revision':0,'action':'issue','file':self.pdf()})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db(); self.assertEqual(item.state, 'issued')
        with patch('accounts.services.deliver', side_effect=TimeoutError) as send:
            service.deliver_pending(); service.deliver_pending()
            self.assertEqual(send.call_count, 1)
            html = send.call_args.kwargs['html_body']
            self.assertIn('GUNSYSTEMS', html)
            self.assertIn('background:#173f35', html)
            self.assertIn('발급된 PDF를 내려받으세요', html)
            self.assertNotIn(item.purpose, html)
            self.assertIn('메일에 첨부하지 않습니다', html)
        self.assertTrue(CertificateNotice.objects.filter(user=user,mail_state='unknown').exists())
        self.assertContains(self.client.get(url), '발급 서류 받기')

    def test_account_history_preserved_with_clear_suspend_path(self):
        from accounts.lifecycle import business_counts, change_access
        item = self.submit()
        person = item.employee
        self.assertEqual(business_counts(person)['증명서·서류 신청'], 1)
        for action in ['delete_access', 'delete_employee']:
            with self.assertRaisesMessage(ValidationError, '사용 중지'):
                change_access(self.admin, person.pk, action, '검증', self.users['member'].email)
        change_access(self.admin, person.pk, 'suspend', '검증 사용 중지')
        self.users['member'].refresh_from_db()
        self.assertFalse(service.visible(self.users['member']).exists())
        self.assertTrue(CertificateRequest.objects.filter(pk=item.pk).exists())
