from django.db import migrations
from django.db.models import F


def baseline(apps, schema_editor):
    Task = apps.get_model('tasks','Task')
    tasks = Task.objects.using(schema_editor.connection.alias)
    tasks.filter(original_due_date__isnull=True).update(original_due_date=F('due_date'))
    tasks.filter(status='done').update(progress=100)
    # Historical completion times and ratings remain unknown; never fabricate them.


class Migration(migrations.Migration):
    dependencies = [('tasks','0004_task_approved_at_task_completed_at_task_delay_reason_and_more')]
    operations = [migrations.RunPython(baseline,migrations.RunPython.noop)]
