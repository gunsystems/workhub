from django.db import migrations


def seed(apps,schema_editor):
    Category=apps.get_model('tasks','WorkCategory')
    for name in ['기술지원 · 방문지원','기술지원 · 원격지원']:
        Category.objects.using(schema_editor.connection.alias).update_or_create(name=name,defaults={'is_active':True})


class Migration(migrations.Migration):
    dependencies=[('tasks','0038_weekly_calendar_reports')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
