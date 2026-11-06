from django import forms
from .models import Task, Employee
from .permissions import assignable_employees
class TaskForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignee'].queryset = assignable_employees(user) if user else Employee.objects.none()

    class Meta:
        model = Task
        fields = ['title','description','assignee','status','importance','priority','start_date','due_date']
        widgets = {'start_date':forms.DateInput(attrs={'type':'date'}),'due_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':4})}
    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('due_date') and data['due_date'] < data['start_date']:
            self.add_error('due_date','마감일은 시작일보다 빠를 수 없습니다.')
        return data
