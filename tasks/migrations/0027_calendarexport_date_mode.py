from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('tasks','0026_assignment_channels_and_auto_supervisor')]
    operations=[migrations.AddField(model_name='calendarexport',name='date_mode',field=models.CharField(default='auto',max_length=12))]
