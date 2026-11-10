"""Clear only same-day/default-start false positives using the immutable first plan."""
from django.db import migrations
from django.utils import timezone
from django.utils.dateparse import parse_date
from zoneinfo import ZoneInfo


def correct_registration_flags(apps,schema_editor):
    Registration=apps.get_model('tasks','ScheduleRegistration')
    ids=[];tasks={}
    for row in Registration.objects.filter(late=True).iterator():
        plan=row.initial_plan or {}
        if plan.get('activity_start') not in [None,'','None']:continue
        try:day=parse_date(plan.get('start_date',''))
        except (ValueError,TypeError):continue
        if day and day==timezone.localtime(row.registered_at,ZoneInfo('Asia/Seoul')).date():
            ids.append(row.pk);tasks[row.pk]=row.task_id
    if not ids:return
    connection=schema_editor.connection;name=str(connection.settings_dict.get('NAME',''))
    if connection.vendor=='sqlite' and name.startswith('/var/data/'):
        import sqlite3
        from pathlib import Path
        from datetime import datetime,timezone as tz
        directory=Path('/var/data/backups');directory.mkdir(exist_ok=True)
        target=directory/('workhub-pre-v0122-'+datetime.now(tz.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        source=sqlite3.connect(name,timeout=30);dest=sqlite3.connect(str(target))
        try:
            source.backup(dest,pages=256,sleep=.05)
            if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Database backup verification failed')
        finally:dest.close();source.close()
        print('Workhub v0.12.2 backup: '+str(target)+' integrity=ok',flush=True)
    count=Registration.objects.filter(pk__in=ids).update(late=False)
    Audit=apps.get_model('tasks','SystemAudit')
    Audit.objects.bulk_create([Audit(action='update',target_type='Task',target_id=str(tasks[pk]),
        target_label='지연 등록 판정 정정',actor_name='시스템',before={'지연 등록':True},
        after={'지연 등록':False,'사유':'당일 업무의 기본 시작시간과 작성 소요시간을 지연 등록으로 판정한 표시를 정정했습니다.'}) for pk in ids])
    print('Corrected same-day default-start registration flags: '+str(count),flush=True)


class Migration(migrations.Migration):
    dependencies=[('tasks','0030_work_documents')]
    operations=[migrations.RunPython(correct_registration_flags,migrations.RunPython.noop)]
