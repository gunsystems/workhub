from django import forms
from .models import Task, Employee
from .permissions import assignable_employees, is_manager
class TaskForm(forms.ModelForm):
    change_reason = forms.CharField(label='마감일 변경 사유', required=False, widget=forms.Textarea(attrs={'rows':2}), max_length=5000)
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['assignee'].queryset = assignable_employees(user) if user else Employee.objects.none()
        self.fields['status'].choices = [choice for choice in Task.STATUS if choice[0] in ['todo','doing','blocked']]
        if self.instance.pk and not is_manager(user):
            self.fields['start_date'].disabled = True
            self.fields['due_date'].disabled = True

    class Meta:
        model = Task
        fields = ['title','description','assignee','status','importance','priority','start_date','due_date']
        widgets = {'start_date':forms.DateInput(attrs={'type':'date'}),'due_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':4})}
    def clean(self):
        data = super().clean()
        if self.instance.pk and data.get('due_date') != self.instance.due_date and not data.get('change_reason'):
            self.add_error('change_reason','변경된 마감일의 사유를 입력하세요.')
        if data.get('start_date') and data.get('due_date') and data['due_date'] < data['start_date']:
            self.add_error('due_date','마감일은 시작일보다 빠를 수 없습니다.')
        return data


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
