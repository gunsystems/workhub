"""Private, sequential financial approvals. Approval never executes a bank payment."""
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q,Sum,OuterRef,Subquery
from django.utils import timezone
from .models import Proposal,ProposalStep,ProposalEvent,ProposalNotice,Employee
from .permissions import profile


def visible(user):
    qs=Proposal.objects.select_related('employee__department','original','task')
    if user.is_superuser:return qs
    person=profile(user)
    if not person:return qs.none()
    scope=Q(employee=person)|Q(steps__approver=person)
    submitted=Q(submitted_at__isnull=False)
    if person.position in ['head','executive']:scope|=submitted
    elif person.position=='leader' or person.role=='manager':scope|=submitted & Q(employee__department_id=person.department_id)
    return qs.filter(scope).distinct()


def current(item):
    if item.state!='pending':return None
    return item.steps.filter(round=item.round,decision='').order_by('order').first()


def pending_for(person):
    if not person or not person.is_active:return Proposal.objects.none()
    step=ProposalStep.objects.filter(proposal_id=OuterRef('pk'),round=OuterRef('round'),decision='').order_by('order')
    return Proposal.objects.filter(state='pending').annotate(current_approver=Subquery(step.values('approver_id')[:1])).filter(current_approver=person.pk).select_related('employee__department')


def event(item,user,action,body=''):
    person=profile(user)
    snapshot={key:str(getattr(item,key) or '') for key in ['title','category','stage','body','amount','needed_on','supplier','specifics','state','revision','round','task_id','original_id']}
    snapshot['documents']=list(item.documents.filter(archived_at__isnull=True).values_list('pk',flat=True))
    snapshot['line_items']=item.line_items
    row=ProposalEvent.objects.create(proposal=item,actor=user,actor_name=str(person) if person else user.get_username(),action=action,body=body,snapshot=snapshot)
    from .audit import write
    write('change','Proposal',item.pk,'품의',after={'상태':item.state,'처리':action,'버전':item.revision},actor=user)
    recipients=[];step=current(item)
    if step and step.approver.user_id and action in ['품의 제출','승인']:recipients.append((step.approver.user,'pending','품의 결재가 필요합니다.'))
    if action in ['승인','반려','지급 기록'] and item.state!='pending' and item.employee.user_id:
        recipients.append((item.employee.user,'result','품의 '+('지급 처리가 기록되었습니다.' if action=='지급 기록' else '결재 결과를 확인해 주세요.')))
    for recipient,kind,message in recipients:
        if recipient.is_active:ProposalNotice.objects.get_or_create(event=row,user=recipient,kind=kind,defaults={'email':recipient.email.lower(),'message':message})
    return row


def chain(item):
    from .leave import route
    if item.employee.position=='executive':return [item.employee]
    people=[]
    for _,person,_ in route(item.employee):
        if person not in people:people.append(person)
    return people


@transaction.atomic
def action(pk,user,kind,revision,body=''):
    item=Proposal.objects.select_for_update().select_related('employee','original').get(pk=pk)
    if not visible(user).filter(pk=pk).exists():raise PermissionDenied
    if item.revision!=revision:raise ValidationError('다른 변경이 반영되었습니다. 새로고침 후 다시 확인하세요.')
    person=profile(user);owner=bool(person and person.pk==item.employee_id)
    if kind=='submit':
        if not owner or item.state not in ['draft','rejected','withdrawn']:raise PermissionDenied
        if not item.title.strip() or not item.body.strip():raise ValidationError('제목과 요청 내용을 입력하세요.')
        if item.amount<0 or ((item.stage=='claim' or item.category!='other') and item.amount<=0):raise ValidationError('비용 요청 금액은 0원보다 커야 합니다. 비용 없는 요청은 기타 품의로 작성하세요.')
        if item.amount>0 and not item.documents.filter(archived_at__isnull=True).exists():raise ValidationError('증빙자료가 필수입니다. 견적서·영수증 파일 또는 증빙 링크를 1개 이상 추가한 뒤 결재 제출해 주세요.')
        if item.original_id:
            original=Proposal.objects.select_for_update().get(pk=item.original_id)
            if item.stage!='claim' or original.stage!='prior' or original.state!='approved' or original.employee_id!=item.employee_id:raise ValidationError('본인의 승인된 사전 품의만 비용 청구에 연결할 수 있습니다.')
            total=original.claims.exclude(pk=item.pk).filter(state__in=['pending','approved']).aggregate(v=Sum('amount'))['v'] or 0
            if total+item.amount>original.amount:raise ValidationError('연결된 품의의 승인 금액을 초과합니다. 추가 비용은 별도 승인 요청으로 제출하세요.')
        people=chain(item);item.round+=1
        for n,approver in enumerate(people):ProposalStep.objects.create(proposal=item,round=item.round,order=n,approver=approver,name=str(approver))
        item.state='pending';item.submitted_at=timezone.now();label='품의 제출'
    elif kind in ['approve','reject']:
        step=current(item)
        if not step or not person or not person.is_active or step.approver_id!=person.pk:raise PermissionDenied
        if kind=='reject' and not body.strip():raise ValidationError('반려 사유를 입력하세요.')
        step.decision=kind;step.decided_at=timezone.now();step.save()
        if kind=='reject':item.state='rejected'
        elif not item.steps.filter(round=item.round,decision='').exists():item.state='approved';item.approved_at=timezone.now()
        label='승인' if kind=='approve' else '반려'
    elif kind=='withdraw':
        if not owner or item.state!='pending':raise PermissionDenied
        item.state='withdrawn';label='신청 회수'
    elif kind=='paid':
        if not user.is_superuser or item.state!='approved' or item.stage!='claim' or item.paid_at:raise PermissionDenied
        if not body.strip():raise ValidationError('지급일·처리 근거를 입력하세요.')
        item.paid_at=timezone.now();label='지급 기록'
    else:raise ValidationError('처리 항목을 확인하세요.')
    if Proposal.objects.filter(pk=pk,revision=revision).update(revision=revision+1)!=1:raise ValidationError('이미 변경된 문서입니다.')
    item.revision+=1;item.save(update_fields=['revision','round','state','submitted_at','approved_at','paid_at'])
    event(item,user,label,body.strip()[:1000]);return item


def deliver_pending(now=None):
    from accounts.lifecycle import account_allowed
    from accounts.services import deliver
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.urls import reverse
    for notice in ProposalNotice.objects.filter(mail_state='pending').select_related('user','event__proposal__employee')[:50]:
        if not ProposalNotice.objects.filter(pk=notice.pk,mail_state='pending').update(mail_state='sending'):continue
        user=notice.user;access=getattr(user,'workhub_access',None);item=notice.event.proposal
        allowed=(account_allowed(user) and user.is_active and access and access.verified_at and user.email and user.email.lower()==access.email.lower()==notice.email and visible(user).filter(pk=item.pk).exists())
        if notice.kind=='pending':
            step=current(item)
            allowed=allowed and step and step.approver.user_id==user.pk and str(item.revision)==notice.event.snapshot.get('revision')
        if not allowed:ProposalNotice.objects.filter(pk=notice.pk).update(mail_state='skipped');continue
        url=settings.WORKHUB_BASE_URL.rstrip('/')+reverse('proposal_detail',args=[item.pk])
        try:deliver('[Workhub 전자결재] '+notice.message,notice.message+'\n'+url,user.email,html_body=render_to_string('tasks/email_proposal.html',{'notice':notice,'url':url}))
        except Exception:state='unknown'
        else:state='sent'
        ProposalNotice.objects.filter(pk=notice.pk).update(mail_state=state)
        from .audit import write
        write('mail_'+state,'ProposalNotice',notice.pk,'품의 메일',after={'발송 상태':state})
