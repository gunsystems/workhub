import io
from unittest.mock import patch
from PIL import Image
from django.test import TestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import File
from tasks.models import Employee,Department,EmployeePhoto,SystemAudit
from tasks.drive_storage import RepositoryError,WorkhubStorage
from .profile_views import PhotoForm

@override_settings(WORKHUB_REQUIRE_HIWORKS=False,WORKHUB_SCHEDULER_ENABLED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CardPhotoTests(TestCase):
    def setUp(self):
        U=get_user_model();self.user=U.objects.create_user('photo_a');self.other=U.objects.create_user('photo_b');self.admin=U.objects.create_superuser('photo_root')
        team=Department.objects.create(name='사진 검증팀');self.e=Employee.objects.create(user=self.user,name='가상 직원',department=team);self.b=Employee.objects.create(user=self.other,name='가상 직원 B',department=team)
        self.client.force_login(self.user)
    def photo(self,fmt='PNG',size=(300,400)):
        b=io.BytesIO();im=Image.new('RGB',size,'blue');im.save(b,format=fmt);return SimpleUploadedFile('private-name.png',b.getvalue(),content_type='image/png')
    def test_photo_is_normalized_to_metadata_free_portrait_jpeg(self):
        form=PhotoForm({}, {'photo':self.photo()});self.assertTrue(form.is_valid(),form.errors)
        result=form.cleaned_data['photo'];im=Image.open(result);self.assertEqual(im.format,'JPEG');self.assertEqual(im.size,(600,760));self.assertFalse(im.getexif());self.assertNotIn('private-name',result.name)
    def test_invalid_and_large_uploads_rejected(self):
        for f in [SimpleUploadedFile('fake.jpg',b'<svg>bad</svg>'),SimpleUploadedFile('large.png',b'x'*(5*1024*1024+1)),self.photo('GIF')]:
            form=PhotoForm({}, {'photo':f});self.assertFalse(form.is_valid())
    @patch('accounts.profile_views.DriveClient')
    @patch('accounts.profile_views.configured',return_value=True)
    def test_self_upload_private_render_and_audit(self,configured,drive):
        drive.return_value.upload.return_value='drive/fake_photo_id'
        r=self.client.post('/accounts/me/',{'action':'photo','photo':self.photo()});self.assertEqual(r.status_code,302)
        obj=EmployeePhoto.objects.get();self.assertEqual(obj.employee,self.e);self.assertEqual(obj.file.name,'drive/fake_photo_id')
        r=self.client.get('/accounts/me/');self.assertContains(r,'data-card-photo');self.assertContains(r,'사원카드 사진');self.assertNotContains(r,'fake_photo_id')
        log=SystemAudit.objects.get(target_type='EmployeePhoto');self.assertEqual(log.after,{'사진 표시':True})
    @patch('accounts.profile_views.DriveClient')
    @patch('accounts.profile_views.configured',return_value=True)
    def test_foreign_upload_and_photo_read_blocked_before_storage(self,configured,drive):
        EmployeePhoto.objects.create(employee=self.b,file='drive/secret_photo')
        self.assertEqual(self.client.post(f'/accounts/employees/{self.b.pk}/profile/',{'action':'photo','photo':self.photo()}).status_code,403)
        with patch.object(WorkhubStorage,'_open') as opened:
            self.assertEqual(self.client.get(f'/accounts/employees/{self.b.pk}/photo/').status_code,403);opened.assert_not_called()
        drive.assert_not_called()
    @patch('accounts.profile_views.DriveClient')
    @patch('accounts.profile_views.configured',return_value=True)
    def test_admin_can_upload_to_existing_employee(self,configured,drive):
        self.client.force_login(self.admin);drive.return_value.upload.return_value='drive/fake_admin_photo'
        r=self.client.post(f'/accounts/employees/{self.e.pk}/profile/',{'action':'photo','photo':self.photo()});self.assertEqual(r.status_code,302);self.assertEqual(EmployeePhoto.objects.get().uploader,self.admin)
    def test_authenticated_photo_endpoint_and_replacement_marker(self):
        first=EmployeePhoto.objects.create(employee=self.e,file='drive/old_photo');new=EmployeePhoto.objects.create(employee=self.e,file='drive/current_photo')
        f=PhotoForm({}, {'photo':self.photo()});self.assertTrue(f.is_valid());data=f.cleaned_data['photo'].read()
        with patch.object(WorkhubStorage,'_open',return_value=File(io.BytesIO(data))) as opened:
            r=self.client.get(f'/accounts/employees/{self.e.pk}/photo/?v={first.pk}');self.assertEqual(r.status_code,200);self.assertEqual(r['Content-Type'],'image/jpeg');self.assertIn('no-store',r['Cache-Control']);self.assertEqual(opened.call_args.args[0],new.file.name)
        self.assertEqual(self.client.post('/accounts/me/',{'action':'remove_photo'}).status_code,302)
        self.assertEqual(self.client.get(f'/accounts/employees/{self.e.pk}/photo/').status_code,404);self.assertEqual(EmployeePhoto.objects.count(),3)
    @patch('accounts.profile_views.DriveClient')
    @patch('accounts.profile_views.configured',return_value=True)
    def test_storage_failure_keeps_existing_photo(self,configured,drive):
        EmployeePhoto.objects.create(employee=self.e,file='drive/original_photo');drive.return_value.upload.side_effect=RepositoryError('저장소 연결 실패')
        r=self.client.post('/accounts/me/',{'action':'photo','photo':self.photo()});self.assertContains(r,'저장소 연결 실패');self.assertEqual(EmployeePhoto.objects.count(),1)
    def test_anonymous_and_missing_csrf_are_rejected(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.user)
        self.assertEqual(c.post('/accounts/me/',{'action':'remove_photo'}).status_code,403)
        c.logout();self.assertEqual(c.get(f'/accounts/employees/{self.e.pk}/photo/').status_code,302)
