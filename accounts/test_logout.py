import re
from django.contrib.auth import get_user_model
from django.test import Client,TestCase,override_settings


@override_settings(DEBUG=False,SESSION_COOKIE_SECURE=True,CSRF_COOKIE_SECURE=True,
                   SECURE_SSL_REDIRECT=True,SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'),
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],WORKHUB_REQUIRE_HIWORKS=False)
class LogoutPageTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('logout-operator',password='test-only')
        self.client=Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        self.proxy={'HTTP_X_FORWARDED_PROTO':'https','HTTP_HOST':'testserver'}

    def open_page(self,path):
        page=self.client.get(path,**self.proxy)
        self.assertEqual(page.status_code,200)
        token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',page.content.decode()).group(1)
        return page,token

    def test_subscription_page_logout_without_origin_uses_page_referrer_policy(self):
        path='/work/calendar/subscription/'
        page,token=self.open_page(path)
        # A browser which omits Origin needs the page's permitted same-origin Referer.
        provenance={} if page.get('Referrer-Policy')=='no-referrer' else {'HTTP_REFERER':'https://testserver'+path}
        response=self.client.post('/logout/',{'csrfmiddlewaretoken':token},**self.proxy,**provenance)
        self.assertEqual(response.status_code,302)
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertEqual(self.client.get('/work/',**self.proxy).status_code,302)

    def test_subscription_settings_keep_external_referrer_private_and_disable_cache(self):
        page,_=self.open_page('/work/calendar/subscription/')
        self.assertEqual(page['Referrer-Policy'],'same-origin')
        self.assertIn('no-store',page['Cache-Control'])
        self.assertContains(page,'하이웍스 일정 연결')
        self.assertNotContains(page,'>하이웍스 일정 구독<')

    def test_https_issue_show_once_and_revoke_from_subscription_page(self):
        import hashlib
        from tasks.models import CalendarExportPolicy,CalendarSubscription
        CalendarExportPolicy.objects.update_or_create(pk=1,defaults={'allow_official':True})
        path='/work/calendar/subscription/'
        page,token=self.open_page(path)
        provenance={} if page.get('Referrer-Policy')=='no-referrer' else {'HTTP_REFERER':'https://testserver'+path}
        response=self.client.post(path,{'action':'issue','csrfmiddlewaretoken':token},**self.proxy,**provenance)
        self.assertEqual(response.status_code,302)
        shown=self.client.get(path,**self.proxy)
        address=re.search(r'id="subscription-url"[^>]*value="([^"]+)"',shown.content.decode()).group(1)
        secret=address.rsplit('/',1)[1][:-4]
        self.assertEqual(CalendarSubscription.objects.get(owner=self.admin).token_digest,hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotContains(self.client.get(path,**self.proxy),secret)
        feed=Client().get('/work/calendar/subscription/'+secret+'.ics',**self.proxy)
        self.assertEqual(feed.status_code,200)
        self.assertEqual(feed['Referrer-Policy'],'no-referrer')
        _,token=self.open_page(path)
        response=self.client.post(path,{'action':'revoke','csrfmiddlewaretoken':token},HTTP_REFERER='https://testserver'+path,**self.proxy)
        self.assertEqual(response.status_code,302)
        self.assertEqual(Client().get('/work/calendar/subscription/'+secret+'.ics',**self.proxy).status_code,404)

    def test_logout_still_rejects_foreign_origin_and_bad_token(self):
        _,token=self.open_page('/work/calendar/subscription/')
        for origin,value in [('https://foreign.example',token),('https://testserver','x'*64)]:
            with self.subTest(origin=origin):
                response=self.client.post('/logout/',{'csrfmiddlewaretoken':value},HTTP_ORIGIN=origin,**self.proxy)
                self.assertEqual(response.status_code,403)
                self.assertIn('_auth_user_id',self.client.session)

    def test_get_logout_cannot_end_session(self):
        response=self.client.get('/logout/',**self.proxy)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'로그아웃 확인')
        self.assertContains(response,'method="post"')
        self.assertIn('_auth_user_id',self.client.session)

    def test_logout_from_other_app_pages_without_origin(self):
        for path in ['/work/','/work/calendar/','/work/reports/','/accounts/administrators/']:
            with self.subTest(path=path):
                self.client.force_login(self.admin)
                page,token=self.open_page(path)
                self.assertNotEqual(page.get('Referrer-Policy'),'no-referrer')
                response=self.client.post('/logout/',{'csrfmiddlewaretoken':token},HTTP_REFERER='https://testserver'+path,**self.proxy)
                self.assertEqual(response.status_code,302)
                self.assertNotIn('_auth_user_id',self.client.session)

    def test_stale_logout_token_recovers_with_fresh_post_not_get(self):
        _,old_token=self.open_page('/work/')
        # A login in another tab rotates the cookie shared with this old page.
        from django.middleware.csrf import _get_new_csrf_string
        self.client.cookies['csrftoken'] = _get_new_csrf_string()
        response=self.client.post('/logout/',{'csrfmiddlewaretoken':old_token},HTTP_ORIGIN='https://testserver',**self.proxy)
        self.assertEqual(response.status_code,403)
        self.assertIn('_auth_user_id',self.client.session)
        self.assertContains(response,'로그아웃 확인',status_code=403)
        self.assertNotContains(response,'다시 시도하기',status_code=403)
        token=re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',response.content.decode()).group(1)
        success=self.client.post('/logout/',{'csrfmiddlewaretoken':token},HTTP_ORIGIN='https://testserver',**self.proxy)
        self.assertEqual(success.status_code,302)
        self.assertNotIn('_auth_user_id',self.client.session)
