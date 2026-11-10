from django.db import migrations, models
from django.utils import timezone

def backup(apps,schema_editor):
    c=schema_editor.connection
    name=str(c.settings_dict.get('NAME',''))
    if c.vendor!='sqlite' or not name.startswith('/var/data/'): return
    import sqlite3
    from pathlib import Path
    from datetime import datetime,timezone as tz
    directory=Path('/var/data/backups');directory.mkdir(exist_ok=True)
    target=directory/('workhub-pre-v0130-'+datetime.now(tz.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
    source=sqlite3.connect(name,timeout=30);dest=sqlite3.connect(str(target))
    try:
        source.backup(dest,pages=256,sleep=.05)
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Database backup verification failed')
    finally: dest.close();source.close()
    print('Workhub v0.13.0 backup: '+str(target)+' integrity=ok',flush=True)

def backfill(apps,schema_editor):
    Participation=apps.get_model('tasks','TaskParticipation')
    for row in Participation.objects.select_related('task').iterator():
        row.assigned_at=row.task.assigned_at or timezone.now()
        row.save(update_fields=['assigned_at'])

class Migration(migrations.Migration):
    atomic=False
    dependencies=[('tasks','0031_correct_default_start_registration')]
    operations=[
        migrations.RunPython(backup,migrations.RunPython.noop),
        migrations.AddField(model_name='taskparticipation',name='assigned_at',field=models.DateTimeField(null=True,editable=False)),
        migrations.RunPython(backfill,migrations.RunPython.noop),
        migrations.AlterField(model_name='taskparticipation',name='assigned_at',field=models.DateTimeField(default=timezone.now,editable=False)),
    ]
