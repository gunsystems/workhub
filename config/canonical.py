"""Move legacy links to the company host without replaying a submitted form."""
from urllib.parse import urlsplit
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html


class CompanyDomainMiddleware:
    legacy_host='gunsystems-workhub-test.onrender.com'
    company_host='workhub.gunsystems.co.kr'

    def __init__(self,get_response):
        self.get_response=get_response

    def __call__(self,request):
        base=settings.WORKHUB_BASE_URL.rstrip('/')
        parsed=urlsplit(base)
        if (parsed.scheme=='https' and parsed.hostname==self.company_host
                and request.get_host().split(':')[0]==self.legacy_host
                and not request.path.startswith(('/healthz/','/static/'))):
            target=base+request.get_full_path()
            if request.method in ['GET','HEAD']:
                response=HttpResponseRedirect(target)
            else:
                response=HttpResponse(format_html(
                    '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>새 주소에서 계속하기 · GUNSYSTEMS Workhub</title>'
                    '<body style="font-family:system-ui;background:#f5f8f6;color:#284a38;padding:32px">'
                    '<main style="max-width:560px;margin:8vh auto;background:white;border:1px solid #dbe6df;border-radius:16px;padding:28px">'
                    '<h2>새 주소에서 계속해 주세요</h2><p>Workhub 주소가 변경됐습니다. 이 요청은 저장되지 않았습니다.</p>'
                    '<p>새 주소에서 로그인한 뒤 내용을 다시 확인하고 저장해 주세요.</p>'
                    '<a style="display:inline-block;background:#286b53;color:white;padding:12px 18px;border-radius:8px" href="{}">새 주소에서 화면 열기</a>'
                    '</main></body></html>',target),status=409)
            response['Cache-Control']='no-store'
            response['Referrer-Policy']='no-referrer'
            return response
        return self.get_response(request)
