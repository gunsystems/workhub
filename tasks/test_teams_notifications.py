import hashlib
import io
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch
from zipfile import ZipFile
import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import AccessProfile
from .models import (TeamsConnection, TeamsDelivery, Department, Employee, Task, ApprovalStep,
                     Proposal, ProposalStep, ProposalNotice, CertificateNotice, QuickRequest)
from . import teams_notifications as notices, teams_transport as transport

TENANT = '11111111-1111-1111-1111-111111111111'
APP = '22222222-2222-2222-2222-222222222222'
SUBJECT = '33333333-3333-3333-3333-333333333333'
SERVICE = 'https://smba.trafficmanager.net/apac/'


@override_settings(WORKHUB_REQUIRE_HIWORKS=False, WORKHUB_SCHEDULER_ENABLED=False,
    WORKHUB_TEAMS_NOTIFICATIONS_ENABLED=True, TEAMS_NOTIFICATION_TENANT_ID=TENANT,
    TEAMS_NOTIFICATION_APP_ID=APP, TEAMS_NOTIFICATION_CLIENT_SECRET='test-only',
    WORKHUB_BASE_URL='https://workhub.example.test')
class TeamsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('teams_member', email='member@example.test')
        cls.other = get_user_model().objects.create_user('teams_other', email='other@example.test')
        cls.admin = get_user_model().objects.create_superuser('teams_admin', email='admin@example.test')
        dept = Department.objects.create(name='알림 검증팀')
        cls.employee = Employee.objects.create(user=cls.user, name='직원', department=dept)
        cls.approver = Employee.objects.create(user=cls.other, name='팀장', department=dept, position='leader')
        for user in (cls.user, cls.other, cls.admin):
            AccessProfile.objects.create(user=user, email=user.email, verified_at=timezone.now(), require_mfa=False)

    def setUp(self):
        cache.clear()
        self.connection = self.link(self.user)
        self.client.force_login(self.user)

    def link(self, user, **kwargs):
        values = dict(enabled=True, tenant=TENANT, app=APP, subject=str(uuid.uuid4()),
            conversation='personal-' + str(user.pk), service_url=SERVICE, email=user.email,
            connected_at=timezone.now())
        values.update(kwargs)
        return TeamsConnection.objects.create(user=user, **values)

    def task(self, **kwargs):
        values = dict(title='내용은 Teams에 보내지 않음', assignee=self.employee, creator=self.other,
            start_date=timezone.localdate(), due_date=timezone.localdate(), assigned_at=timezone.now(), workflow_version=2)
        values.update(kwargs)
        return Task.objects.create(**values)

    def assignment(self):
        item = self.task()
        notices.assignment(item)
        return item, TeamsDelivery.objects.get()

    def signed(self, activity=None, claims=None, endorsement=True):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        jwk.update(kid='test-key', endorsements=['msteams'] if endorsement else [])
        self.keys = patch('tasks.teams_transport._keys', return_value=[jwk])
        self.keys.start()
        self.addCleanup(self.keys.stop)
        data = {'channelId': 'msteams', 'type': 'message', 'serviceUrl': SERVICE,
            'conversation': {'conversationType': 'personal', 'id': 'new-personal', 'tenantId': TENANT},
            'channelData': {'tenant': {'id': TENANT}}, 'recipient': {'id': '28:' + APP},
            'from': {'id': '29:person', 'aadObjectId': SUBJECT}}
        data.update(activity or {})
        payload = {'iss': transport.ISSUER, 'aud': APP, 'exp': int(time.time()) + 600,
                   'nbf': int(time.time()) - 30, 'serviceurl': SERVICE}
        payload.update(claims or {})
        token = jwt.encode(payload, self.key, algorithm='RS256', headers={'kid': 'test-key'})
        return data, 'Bearer ' + token

    def post_activity(self, data, bearer):
        self.client.logout()
        return self.client.post(reverse('teams_messages'), json.dumps(data), content_type='application/json', HTTP_AUTHORIZATION=bearer)

    def test_disabled_default_does_not_queue_or_call_provider(self):
        with override_settings(WORKHUB_TEAMS_NOTIFICATIONS_ENABLED=False), patch('tasks.teams_transport.send') as send:
            notices.assignment(self.task())
            notices.deliver_pending()
            self.assertFalse(TeamsDelivery.objects.exists())
            send.assert_not_called()

    def test_assignment_independent_from_email_flag_and_deduplicated(self):
        from .assignment_mail import enqueue
        from .models import AssignmentMail
        item = self.task(assignment_email=False)
        with patch('tasks.teams_transport.send') as send:
            enqueue(item, self.other)
            enqueue(item, self.other)
        send.assert_not_called()
        self.assertEqual(TeamsDelivery.objects.count(), 1)
        self.assertFalse(AssignmentMail.objects.exists())

    def test_assignment_rolls_back_with_work_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                notices.assignment(self.task())
                raise RuntimeError()
        self.assertFalse(TeamsDelivery.objects.exists())

    def test_opt_in_and_category_required(self):
        self.connection.enabled = False
        self.connection.save()
        notices.assignment(self.task())
        self.assertFalse(TeamsDelivery.objects.exists())
        self.connection.enabled = True
        self.connection.assignment = False
        self.connection.save()
        notices.assignment(self.task())
        self.assertFalse(TeamsDelivery.objects.exists())

    @patch('tasks.teams_transport.send')
    def test_success_has_private_generic_card_and_link(self, send):
        item, job = self.assignment()
        send.return_value = Mock(status_code=201, json=lambda: {'id': 'received-id'})
        notices.deliver_pending()
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'sent')
        self.assertEqual(job.message_id, 'received-id')
        self.assertEqual(send.call_count, 1)
        self.assertNotIn(item.title, send.call_args.args[1])
        self.assertTrue(send.call_args.args[2].startswith('https://workhub.example.test/'))

    @patch('tasks.teams_transport.send')
    def test_reassignment_email_change_disabled_user_and_relink_fail_closed(self, send):
        for mutation in ('assignment', 'email', 'disabled', 'generation'):
            with self.subTest(mutation=mutation):
                TeamsDelivery.objects.all().delete()
                self.user.refresh_from_db()
                self.user.email = 'member@example.test'
                self.user.is_active = True
                self.user.save()
                self.connection.refresh_from_db()
                self.connection.email = self.user.email
                self.connection.save()
                item, job = self.assignment()
                if mutation == 'assignment':
                    item.assignee = self.approver
                    item.save()
                elif mutation == 'email':
                    get_user_model().objects.filter(pk=self.user.pk).update(email='changed@example.test')
                elif mutation == 'disabled':
                    get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
                else:
                    TeamsConnection.objects.filter(pk=self.connection.pk).update(generation=1)
                notices.deliver_pending()
                job.refresh_from_db()
                self.assertEqual(job.state, 'skipped')
        send.assert_not_called()

    @patch('tasks.teams_transport.send', side_effect=requests.Timeout())
    def test_timeout_is_not_retried(self, send):
        _, job = self.assignment()
        notices.deliver_pending()
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'unknown')
        self.assertEqual(send.call_count, 1)

    @patch('tasks.teams_transport.send')
    def test_throttle_retry_revalidates_permission(self, send):
        item, job = self.assignment()
        send.return_value = Mock(status_code=429, headers={'Retry-After': '40'})
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'pending')
        self.assertGreater(job.due_at, timezone.now())
        item.status = 'cancelled'
        item.save()
        TeamsDelivery.objects.filter(pk=job.pk).update(due_at=timezone.now())
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'skipped')
        self.assertEqual(send.call_count, 1)

    @patch('tasks.teams_transport.send')
    def test_expired_and_interrupted_jobs_are_not_sent(self, send):
        _, job = self.assignment()
        TeamsDelivery.objects.filter(pk=job.pk).update(created_at=timezone.now()-timedelta(days=2))
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'skipped')
        TeamsDelivery.objects.filter(pk=job.pk).update(state='sending', claimed_until=timezone.now()-timedelta(minutes=1))
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'unknown')
        send.assert_not_called()

    def test_signed_personal_channel_and_service_claim(self):
        data, token = self.signed()
        self.assertEqual(transport.validate_activity(token, data)[:2], (TENANT, APP))
        for change in ({'serviceUrl': 'https://evil.test/'}, {'conversation': {'conversationType': 'channel'}},
                       {'channelData': {'tenant': {'id': str(uuid.uuid4())}}}, {'recipient': {'id': '28:other'}}):
            with self.subTest(change=change), self.assertRaises(Exception):
                transport.validate_activity(token, {**data, **change})

    def test_wrong_audience_expiry_signature_and_endorsement_rejected(self):
        for claims in ({'aud': str(uuid.uuid4())}, {'exp': 1}, {'iss': 'https://evil.test'}):
            data, token = self.signed(claims=claims)
            with self.assertRaises(Exception):
                transport.validate_activity(token, data)
            self.keys.stop()
        data, token = self.signed(endorsement=False)
        with self.assertRaises(Exception):
            transport.validate_activity(token, data)

    def test_safe_service_urls(self):
        for value in ('http://smba.trafficmanager.net/', 'https://smba.trafficmanager.net.evil.test/',
                      'https://smba.trafficmanager.net:443/', 'https://smba.trafficmanager.net/../',
                      'https://smba.trafficmanager.net/apac/?x=1', 'https://user@smba.trafficmanager.net/',
                      'https://smba.trafficmanager.net/%2e%2e/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                transport.safe_service_url(value)

    def code(self):
        response = self.client.post(reverse('teams_preferences'), {'action': 'link'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        code = response.context['code']
        self.connection.refresh_from_db()
        self.assertNotEqual(self.connection.code_hash, code)
        return code

    def test_link_code_single_use_and_no_historical_delivery(self):
        code = self.code()
        data, token = self.signed({'text': '연결 ' + code})
        self.assertEqual(self.post_activity(data, token).status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.subject, SUBJECT)
        self.assertIsNone(self.connection.code_hash)
        generation = self.connection.generation
        self.post_activity(data, token)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.generation, generation)
        self.assertFalse(TeamsDelivery.objects.exists())

    def test_expired_code_does_not_link(self):
        code = self.code()
        TeamsConnection.objects.filter(pk=self.connection.pk).update(code_expires_at=timezone.now()-timedelta(seconds=1))
        data, token = self.signed({'text': '연결 ' + code})
        self.post_activity(data, token)
        self.connection.refresh_from_db()
        self.assertNotEqual(self.connection.subject, SUBJECT)

    def test_existing_microsoft_identity_cannot_be_replaced(self):
        from .models import DocumentConnection
        DocumentConnection.objects.create(user=self.user, tenant=TENANT, client=APP,
            subject=str(uuid.uuid4()), email=self.user.email)
        code = self.code()
        data, token = self.signed({'text': '연결 ' + code})
        self.post_activity(data, token)
        self.connection.refresh_from_db()
        self.assertNotEqual(self.connection.subject, SUBJECT)

    def test_uninstall_revokes_connection_and_pending_deliveries(self):
        _, job = self.assignment()
        data, token = self.signed({'type': 'installationUpdate', 'action': 'remove',
            'conversation': {'conversationType': 'personal', 'id': self.connection.conversation, 'tenantId': TENANT}})
        self.post_activity(data, token)
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.assertIsNone(self.connection.connected_at)
        with patch('tasks.teams_transport.send') as send:
            notices.deliver_pending()
        send.assert_not_called()

    def test_anonymous_endpoint_rejects_unsigned_post_and_get(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('teams_messages')).status_code, 405)
        self.assertEqual(self.client.post(reverse('teams_messages'), '{}', content_type='application/json').status_code, 401)

    def test_employee_cannot_access_administration_or_manifest(self):
        for route in ('teams_administration', 'teams_manifest'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
        self.assertEqual(self.client.get(reverse('teams_preferences')).status_code, 200)

    def test_csrf_required_for_settings(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('teams_preferences'), {'action': 'disconnect'}).status_code, 403)

    def test_manifest_only_personal_bot_and_no_graph_permission(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('teams_manifest'))
        self.assertEqual(response.status_code, 200)
        package = ZipFile(io.BytesIO(response.content))
        manifest = json.loads(package.read('manifest.json'))
        self.assertEqual(manifest['bots'][0]['scopes'], ['personal'])
        self.assertEqual(manifest['permissions'], [])
        self.assertNotIn('webApplicationInfo', manifest)
        self.assertEqual(set(package.namelist()), {'manifest.json', 'color.png', 'outline.png'})

    def test_proposal_pending_only_current_turn_and_mail_preserved(self):
        from .proposals import event
        self.link(self.other)
        proposal = Proposal.objects.create(employee=self.employee, title='비공개 품의', category='purchase',
            body='비공개 사유', amount=2000000, state='pending', round=1, revision=1)
        step = ProposalStep.objects.create(proposal=proposal, round=1, order=1, approver=self.approver)
        event(proposal, self.user, '품의 제출')
        row = ProposalNotice.objects.get()
        self.assertEqual(row.mail_state, 'pending')
        job = TeamsDelivery.objects.get()
        self.assertEqual(job.user_id, self.other.pk)
        self.assertIsNotNone(notices.destination(job))
        step.decision = 'approved'
        step.save()
        self.assertIsNone(notices.destination(job))

    def test_task_approval_turn_and_results(self):
        from .workflow import record
        self.link(self.other)
        task = self.task(status='review', report_round=1)
        step = ApprovalStep.objects.create(task=task, approver=self.other, approver_name='팀장', position=1, stage=1, decision='pending')
        record(task, self.user, 'submitted', '완료 보고')
        job = TeamsDelivery.objects.get()
        self.assertEqual(job.user_id, self.other.pk)
        self.assertIsNotNone(notices.destination(job))
        step.decision = 'approved'
        step.save()
        task.status = 'done'
        task.save()
        self.assertIsNone(notices.destination(job))
        record(task, self.other, 'approved', '승인')
        self.assertTrue(TeamsDelivery.objects.filter(user=self.user, kind='result').exists())

    def test_quick_assignment_and_result(self):
        from .quick import record
        self.link(self.other)
        item = QuickRequest.objects.create(title='간단 요청', body='내용', requester=self.other, assignee=self.employee)
        record(item, self.other, '전달')
        self.assertEqual(TeamsDelivery.objects.get().kind, 'assignment')
        item.state = 'replied'
        item.revision += 1
        item.save()
        record(item, self.user, '결과 회신')
        self.assertTrue(TeamsDelivery.objects.filter(user=self.other, kind='result').exists())

    def test_leave_pending_and_result_keep_existing_mail_outbox(self):
        from .models import LeaveRequest, LeaveApproval, LeaveEvent, LeaveNotice
        from .leave_notifications import enqueue
        self.link(self.other)
        item = LeaveRequest.objects.create(employee=self.employee, start=timezone.localdate(),
            end=timezone.localdate(), kind='annual', days=1, reason='비공개 사유', revision=1)
        step = LeaveApproval.objects.create(request=item, order=1, role='leader', approver=self.approver)
        event = LeaveEvent.objects.create(request=item, actor=self.user, action='휴가 신청')
        enqueue(event)
        enqueue(event)
        self.assertEqual(LeaveNotice.objects.count(), 1)
        self.assertEqual(TeamsDelivery.objects.count(), 1)
        pending = TeamsDelivery.objects.get()
        self.assertIsNotNone(notices.destination(pending))
        step.decision = 'approved'
        step.save()
        item.state = 'approved'
        item.revision = 2
        item.save()
        self.assertIsNone(notices.destination(pending))
        event = LeaveEvent.objects.create(request=item, actor=self.other, action='승인')
        enqueue(event)
        result = TeamsDelivery.objects.get(kind='result')
        self.assertEqual(result.user_id, self.user.pk)
        self.assertIsNotNone(notices.destination(result))
        self.assertFalse(LeaveNotice.objects.exclude(mail_state='pending').exists())

    def test_certificate_handler_and_private_result(self):
        from .models import CertificateRequest
        from .certificates import event
        self.link(self.other)
        item = CertificateRequest.objects.create(employee=self.employee, submission_key=uuid.uuid4(),
            kind='employment', purpose='비공개 용도', assignee=self.other)
        event(item, self.user, '서류 신청', notify='pending')
        pending = TeamsDelivery.objects.get()
        self.assertEqual(pending.user_id, self.other.pk)
        self.assertIsNotNone(notices.destination(pending))
        item.state = 'issued'
        item.revision = 1
        item.save()
        self.assertIsNone(notices.destination(pending))
        event(item, self.other, '발급 완료', notify='result')
        result = TeamsDelivery.objects.get(kind='result')
        self.assertEqual(result.user_id, self.user.pk)
        self.assertIsNotNone(notices.destination(result))
        self.assertFalse(CertificateNotice.objects.exclude(mail_state='pending').exists())

    def test_same_microsoft_account_cannot_bind_two_workhub_users(self):
        self.link(self.other, subject=SUBJECT)
        code = self.code()
        data, token = self.signed({'text': '연결 ' + code})
        self.post_activity(data, token)
        self.connection.refresh_from_db()
        self.assertNotEqual(self.connection.subject, SUBJECT)

    @patch('tasks.teams_transport.send')
    def test_preferences_change_invalidates_queued_messages(self, send):
        _, job = self.assignment()
        self.client.post(reverse('teams_preferences'), {'action': 'save', 'assignment': 'on', 'pending': 'on', 'result': 'on'})
        self.client.post(reverse('teams_preferences'), {'action': 'save', 'enabled': 'on', 'assignment': 'on', 'pending': 'on', 'result': 'on'})
        notices.deliver_pending()
        job.refresh_from_db()
        self.assertEqual(job.state, 'skipped')
        send.assert_not_called()

    def test_invalid_signature_and_missing_validity_claim_rejected(self):
        data, token = self.signed()
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload = {'iss': transport.ISSUER, 'aud': APP, 'exp': int(time.time())+600,
                   'nbf': int(time.time())-30, 'serviceurl': SERVICE}
        forged = jwt.encode(payload, other_key, algorithm='RS256', headers={'kid': 'test-key'})
        with self.assertRaises(Exception):
            transport.validate_activity('Bearer '+forged, data)
        del payload['exp']
        missing = jwt.encode(payload, self.key, algorithm='RS256', headers={'kid': 'test-key'})
        with self.assertRaises(Exception):
            transport.validate_activity('Bearer '+missing, data)
