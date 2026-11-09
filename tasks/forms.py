from django import forms
from django.db.models import Q
from .models import Task, Employee, Project, WorkRequest, ChecklistItem, WorkCategory
from .permissions import assignable_employees, is_manager, visible_projects, visible_departments, visible_employees, is_company_admin
class TaskForm(forms.ModelForm):
    new_category = forms.CharField(label='새 업무 종류 (선택)', max_length=40, required=False, help_text='목록에 없는 종류를 추가합니다. 기존 종류와 중복되면 같은 종류를 사용합니다.')
    change_reason = forms.CharField(label='마감일 변경 사유', required=False, widget=forms.Textarea(attrs={'rows':2}), max_length=5000)
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['management_mode'].required = False
        self.fields['category'].queryset = WorkCategory.objects.filter(Q(is_active=True)|Q(pk=self.instance.category_id))
        self.fields['report_days'].required = False
        self.fields['review_days'].required = False
        self.fields['project'].queryset = visible_projects(user) if user else Project.objects.none()
        self.fields['assignee'].queryset = assignable_employees(user) if user else Employee.objects.none()
        allowed = self.fields['assignee'].queryset
        active = Q(is_active=True) & (Q(user__isnull=True) | Q(user__is_active=True))
        self.fields['assignee'].queryset = allowed.filter(active | Q(pk=self.instance.assignee_id)) if self.instance.pk else allowed.filter(active)
        self.fields['status'].choices = [choice for choice in Task.STATUS if choice[0] in ['todo','doing','blocked']]
        if self.instance.pk and not is_manager(user):
            self.fields['start_date'].disabled = True
            self.fields['due_date'].disabled = True
            self.fields['report_days'].disabled = True
            self.fields['review_days'].disabled = True

    class Meta:
        model = Task
        fields = ['title','description','project','completion_criteria','report_days','review_days','assignee','status','importance','priority','start_date','due_date','category','management_mode','activity_start','activity_end','location','customer','work_style','estimated_minutes','next_action','next_check_date']
        widgets = {'start_date':forms.DateInput(attrs={'type':'date'}),'due_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':4}),
            'activity_start':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}),
            'activity_end':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}),
            'next_check_date':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        data = super().clean()
        data['management_mode'] = data.get('management_mode') or self.instance.management_mode or 'report'
        start_time,end_time = data.get('activity_start'),data.get('activity_end')
        if bool(start_time) != bool(end_time): self.add_error('activity_end','시작·종료 시간을 모두 입력하거나 모두 비워 종일 일정으로 등록하세요.')
        if start_time and end_time and end_time <= start_time: self.add_error('activity_end','종료는 시작보다 늦어야 합니다.')
        if data.get('estimated_minutes') is not None and not 1 <= data['estimated_minutes'] <= 525600:
            self.add_error('estimated_minutes','예상 소요시간은 1~525600분으로 입력하세요.')
        if start_time and end_time and data.get('start_date') and data.get('due_date'):
            from django.utils import timezone
            if timezone.localtime(start_time).date() < data['start_date'] or timezone.localtime(end_time).date() > data['due_date']:
                self.add_error('activity_end','수행 시간은 시작일과 마감일 사이로 지정하세요.')
        if data.get('category') and data.get('new_category'): self.add_error('new_category','기존 종류 선택과 새 종류 추가 중 하나만 사용하세요.')
        if data.get('new_category') and WorkCategory.objects.filter(name=data['new_category'].strip(),is_active=False).exists():self.add_error('new_category','사용 중지된 업무 종류입니다. 다른 이름을 사용하세요.')
        data['report_days'] = data.get('report_days') or self.instance.report_days or 3
        data['review_days'] = data.get('review_days') or self.instance.review_days or 2
        project, assignee = data.get('project'), data.get('assignee')
        # A project's owning department may delegate work to another team.
        # Project selection and employee assignment still use their own permission scopes.
        if project and data.get('start_date') and data.get('due_date') and (data['start_date'] < project.start_date or data['due_date'] > project.due_date):
            self.add_error('project','업무 기간은 프로젝트 기간 안에 지정하세요.')
        if self.instance.pk and data.get('due_date') != self.instance.due_date and not data.get('change_reason'):
            self.add_error('change_reason','변경된 마감일의 사유를 입력하세요.')
        if data.get('start_date') and data.get('due_date') and data['due_date'] < data['start_date']:
            self.add_error('due_date','마감일은 시작일보다 빠를 수 없습니다.')
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        name = self.cleaned_data.get('new_category','').strip()
        if name:
            category,created = WorkCategory.objects.get_or_create(name=name,defaults={'created_by':self.user})
            if not category.is_active:
                from django.core.exceptions import ValidationError
                raise ValidationError('사용 중지된 업무 종류입니다. 다른 이름을 사용하세요.')
            obj.category = category
        if commit: obj.save(); self.save_m2m()
        return obj


class ProgressForm(forms.Form):
    progress = forms.IntegerField(label='진행률 (%)', min_value=0, max_value=99)
    body = forms.CharField(label='수행 내용 · 다음 계획', widget=forms.Textarea(attrs={'rows':4}), max_length=10000)
    delay_reason = forms.CharField(label='지연·장애 사유', required=False, widget=forms.Textarea(attrs={'rows':2}), max_length=5000)


class SubmitForm(forms.Form):
    body = forms.CharField(label='완료 결과 · 결과물 위치', widget=forms.Textarea(attrs={'rows':4}), max_length=10000)


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(label='검토 결과', choices=[('approve','완료 승인'),('return','보완 요청')])
    quality_score = forms.IntegerField(label='결과 평가 (0~100)', min_value=0, max_value=100, required=False)
    body = forms.CharField(label='평가 근거 · 피드백', widget=forms.Textarea(attrs={'rows':4}), max_length=10000)
    def clean(self):
        data = super().clean()
        if data.get('decision') == 'approve' and data.get('quality_score') is None:
            self.add_error('quality_score','완료 승인 시 결과 평가를 입력하세요.')
        return data


class FeedbackForm(forms.Form):
    body = forms.CharField(label='관리자 피드백', widget=forms.Textarea(attrs={'rows':3}), max_length=10000)


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name','department','owner','description','start_date','due_date']
        widgets = {'start_date':forms.DateInput(attrs={'type':'date'}),'due_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['department'].queryset = visible_departments(user)
        self.fields['owner'].queryset = assignable_employees(user)
        allowed = self.fields['owner'].queryset
        active = Q(is_active=True) & (Q(user__isnull=True) | Q(user__is_active=True))
        self.fields['owner'].queryset = allowed.filter(active | Q(pk=self.instance.owner_id)) if self.instance.pk else allowed.filter(active)
        if not is_company_admin(user):
            self.fields['owner'].queryset = self.fields['owner'].queryset.filter(user=user)
        if self.instance.pk:
            self.fields['department'].disabled = True
    def clean(self):
        data = super().clean()
        owner,department = data.get('owner'),data.get('department')
        if owner and department and owner.department_id != department.pk:
            self.add_error('owner','총괄 담당자는 프로젝트 부서 소속이어야 합니다.')
        start,end = data.get('start_date'),data.get('due_date')
        if start and end and end < start:
            self.add_error('due_date','마감일은 시작일보다 빠를 수 없습니다.')
        if self.instance.pk and start and end and self.instance.tasks.filter(Q(start_date__lt=start) | Q(due_date__gt=end)).exists():
            self.add_error('due_date','기존 업무 기간을 포함하는 프로젝트 기간을 지정하세요.')
        return data


class WorkRequestForm(forms.ModelForm):
    class Meta:
        model = WorkRequest
        fields = ['title','description','department','project','requested_due_date']
        widgets = {'requested_due_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':4})}
    def __init__(self,*args,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['department'].queryset = visible_departments(user)
        self.fields['project'].queryset = visible_projects(user)
    def clean(self):
        data = super().clean()
        project,department = data.get('project'),data.get('department')
        if project and department and project.department_id != department.pk:
            self.add_error('project','요청 부서와 프로젝트 부서가 같아야 합니다.')
        return data


class ChecklistForm(forms.ModelForm):
    class Meta:
        model = ChecklistItem
        fields = ['title','required']


class CloseRequestForm(forms.Form):
    reason = forms.CharField(label='취소 사유',max_length=2000,widget=forms.Textarea(attrs={'rows':3}))
