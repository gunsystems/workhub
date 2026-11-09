"""Read-only directory listing and explicit Workhub employee registration."""
from django.core.exceptions import ValidationError
from tasks.models import Employee, HiworksLink


def employee_for_row(row, employees):
    matches=[]
    for employee in employees:
        link=getattr(employee,'hiworkslink',None)
        invitation=getattr(employee,'invitation',None)
        emails=[employee.user.email if employee.user_id else '', invitation.email if invitation else '']
        if link:
            if link.remote_id==row['remote_id'] and link.email.lower()==row['email'].lower():
                matches.append(employee)
            # An existing immutable identity must never be replaced by an email match.
            continue
        if row['email'].lower() in [value.lower() for value in emails if value]:
            matches.append(employee)
    if len(matches)>1: raise ValidationError('같은 이메일의 직원 기록이 중복되어 있습니다. 관리자에게 연결 확인을 요청하세요.')
    return matches[0] if matches else None


def staff_directory_rows(directory, actor):
    from .admin import invitation_state
    employees=list(Employee.objects.select_related('department','user','invitation','hiworkslink'))
    rows=[];matched=set()
    for source in directory:
        row=dict(source)
        try: employee=employee_for_row(row,employees)
        except ValidationError:
            row.update(employee=None,state='연결 확인 필요',conflict=True);rows.append(row);continue
        if employee: matched.add(employee.pk)
        protected=bool(employee and employee.user_id and (employee.user.is_superuser or (employee.user.is_staff and not actor.is_superuser)))
        row.update(employee=employee,protected=protected,can_edit=bool(employee and not protected),
            can_invite=bool(employee and not protected and not employee.user_id and employee.is_active),
            state=invitation_state(None,employee) if employee else (source.get('state') or '미등록'),
            invitation=getattr(employee,'invitation',None) if employee else None)
        # A mailbox attached to an unrelated account is blocked by invitation_directory.
        row['conflict']=bool(not employee and source.get('state'))
        rows.append(row)
    unlinked=[employee for employee in employees if employee.pk not in matched and not employee.name.endswith(' (예시)')]
    return rows,unlinked


def save_directory_link(employee,row):
    if HiworksLink.objects.filter(remote_id=row['remote_id']).exclude(employee=employee).exists():
        raise ValidationError('이미 등록된 하이웍스 직원입니다. 직원 관리에서 기존 기록을 확인하세요.')
    return HiworksLink.objects.create(employee=employee,email=row['email'],remote_id=row['remote_id'],user_id=row['user_id'])
