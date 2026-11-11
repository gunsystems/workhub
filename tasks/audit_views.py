import json
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from .models import SystemAudit, AssignmentMail
from .permissions import is_company_admin
from .audit_display import decorate

ACTIONS=[('','전체 작업'),('create','생성'),('update','변경'),('delete','삭제'),('restore','복구'),('work_event','업무 처리'),
         ('login','로그인'),('logout','로그아웃'),('session_timeout','무활동 만료'),('mail_sent','메일 발송'),
         ('auto_supervisor','직속 상위자 자동 연결'),('notice_scheduled','공지 발송 확정'),('notice_cancelled','공지 발송 취소'),('notice_published','공지 게시'),('notice_delivery','공지 전달 결과'),('mail_unknown','메일 결과 확인 필요'),('mail_skipped','메일 발송 제외')]

class AuditFilter(forms.Form):
    q=forms.CharField(label='대상·처리자 검색',required=False,max_length=120)
    action=forms.ChoiceField(label='작업',required=False,choices=ACTIONS)
    start=forms.DateField(label='시작일',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='종료일',required=False,widget=forms.DateInput(attrs={'type':'date'}))

@login_required
def audit_list(request):
    if not is_company_admin(request.user): raise PermissionDenied
    form=AuditFilter(request.GET)
    records=SystemAudit.objects.all()
    if form.is_valid():
        data=form.cleaned_data
        if data['q']: records=records.filter(Q(target_label__icontains=data['q'])|Q(actor_name__icontains=data['q'])|Q(target_id=data['q']))
        if data['action']: records=records.filter(action=data['action'])
        if data['start']: records=records.filter(created_at__date__gte=data['start'])
        if data['end']: records=records.filter(created_at__date__lte=data['end'])
    else: records=records.none()
    page=Paginator(records,30).get_page(request.GET.get('page'))
    labels=dict(ACTIONS)
    for event in page:
        event.action_label=labels.get(event.action,event.action)
        decorate(event)
        event.before_text=json.dumps(event.before,ensure_ascii=False,indent=2)
        event.after_text=json.dumps(event.after,ensure_ascii=False,indent=2)
    query=request.GET.copy(); query.pop('page',None)
    return render(request,'tasks/system_audit.html',{'form':form,'page':page,'query':query.urlencode(),
        'mail_rows':AssignmentMail.objects.select_related('task','employee').order_by('-pk')[:20]})
