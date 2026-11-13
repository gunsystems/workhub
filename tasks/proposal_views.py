from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,render,redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import Proposal,ProposalNotice,Employee
from .permissions import profile,visible_tasks
from .leave_views import employee_access
from . import proposals


class ProposalForm(forms.ModelForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=0)
    class Meta:
        model=Proposal
        fields=['title','category','stage','body','amount','needed_on','supplier','specifics','task','original']
        widgets={'body':forms.Textarea(attrs={'rows':5}),'specifics':forms.Textarea(attrs={'rows':3}),'needed_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),'amount':forms.NumberInput(attrs={'min':'0','step':'1','inputmode':'numeric'})}
        labels={'task':'관련 업무 참조','original':'승인된 사전 품의 연결'}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        person=profile(user)
        self.fields['task'].label='관련 업무 참조';self.fields['original'].label='승인된 사전 품의 연결'
        self.fields['task'].queryset=visible_tasks(user)
        self.fields['original'].queryset=Proposal.objects.filter(employee=person,state='approved',stage='prior')
        self.fields['revision'].initial=self.instance.revision if self.instance.pk else 0
        self.fields['amount'].help_text='부가세를 포함한 총액입니다. 비용 없는 기타 품의는 0원으로 입력하세요.'
        self.fields['specifics'].help_text='구매: 품목·수량 / 교육·자격증: 과정·시험명·일정 / 복지: 지원 항목·대상을 적어 주세요.'
        self.fields['original'].help_text='사용 후 비용 청구일 때 본인의 승인된 품의를 연결할 수 있습니다.'
    def clean(self):
        data=super().clean()
        if data.get('amount') is not None and data['amount']<0:self.add_error('amount','0원 이상으로 입력하세요.')
        if data.get('original') and data.get('stage')!='claim':self.add_error('original','사용 후 비용 청구에서만 연결하세요.')
        return data


@login_required
@never_cache
@employee_access
@require_http_methods(['GET','POST'])
def edit(request,pk=None):
    person=profile(request.user)
    if not person:raise PermissionDenied
    item=get_object_or_404(Proposal,pk=pk,employee=person) if pk else Proposal(employee=person)
    if item.state not in ['draft','withdrawn','rejected']:raise PermissionDenied
    initial={}
    if not pk and request.GET.get('original','').isdigit():
        old=get_object_or_404(Proposal,pk=request.GET['original'],employee=person,state='approved',stage='prior')
        initial={'original':old.pk,'title':old.title+' · 비용 청구','category':old.category,'stage':'claim','body':old.body,'amount':old.amount,'supplier':old.supplier,'specifics':old.specifics,'task':old.task_id}
    form=ProposalForm(request.POST if request.method=='POST' else None,user=request.user,instance=item,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if pk:
                    old=Proposal.objects.select_for_update().get(pk=pk)
                    if old.revision!=form.cleaned_data['revision'] or old.state not in ['draft','withdrawn','rejected']:raise ValidationError('다른 변경이 반영되었습니다. 새로고침 후 다시 작성하세요.')
                item=form.save(commit=False);item.revision+=1;item.save();proposals.event(item,request.user,'임시 저장')
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('proposal_detail',pk=item.pk)
    return render(request,'tasks/proposal_form.html',{'form':form,'item':item})


@login_required
@never_cache
@employee_access
@require_http_methods(['GET','POST'])
def detail(request,pk):
    item=get_object_or_404(proposals.visible(request.user),pk=pk);error=''
    if request.method=='POST':
        try:
            try:revision=int(request.POST.get('revision',''))
            except ValueError:raise ValidationError('새로고침 후 다시 처리하세요.')
            proposals.action(pk,request.user,request.POST.get('action',''),revision,request.POST.get('body',''))
        except ValidationError as exc:error=' / '.join(exc.messages)
        else:return redirect('proposal_detail',pk=pk)
        item.refresh_from_db()
    ProposalNotice.objects.filter(event__proposal=item,user=request.user,read_at__isnull=True).update(read_at=timezone.now())
    person=profile(request.user);owner=bool(person and item.employee_id==person.pk);step=proposals.current(item)
    return render(request,'tasks/proposal_detail.html',{'item':item,'owner':owner,'error':error,'editable':owner and item.state in ['draft','rejected','withdrawn'],'can_decide':bool(person and step and step.approver_id==person.pk),'steps':item.steps.filter(round=item.round),'events':item.events.all(),'documents':item.documents.filter(archived_at__isnull=True)})
