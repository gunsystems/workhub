from django.db import migrations


def prepare_departments(apps, schema_editor):
    Department = apps.get_model('tasks', 'Department')
    Employee = apps.get_model('tasks', 'Employee')
    alias = schema_editor.connection.alias
    departments = Department.objects.using(alias)
    employees = Employee.objects.using(alias)
    for name in ['영업팀', '보안기술팀', '경영지원팀']:
        departments.get_or_create(name=name)
    security = departments.get(name='보안기술팀')
    # Consolidate only known demo employees; preserve all real employee records.
    for old_name in ['개발팀', '기술지원팀']:
        for old in departments.filter(name=old_name):
            employees.filter(department=old, name__endswith=' (예시)').update(department=security)
            if not employees.filter(department=old).exists():
                old.delete()


class Migration(migrations.Migration):
    dependencies = [('tasks', '0002_employee_role_employee_user')]
    operations = [migrations.RunPython(prepare_departments, migrations.RunPython.noop)]
