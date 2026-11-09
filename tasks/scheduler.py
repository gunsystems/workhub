"""Persistent, idempotent scheduler run inside the existing always-on web worker."""
import logging,secrets,threading
from datetime import timedelta
from django.conf import settings
from django.db import transaction,close_old_connections,OperationalError
from django.utils import timezone
from .models import PersonalSchedule,PersonalReminder,SchedulerLease,GoogleCalendarConnection
from .schedules import next_run,create_occurrence
from accounts.lifecycle import account_allowed

log=logging.getLogger(__name__)
_stop=None


@transaction.atomic
def process_rule(pk,now):
    rule=PersonalSchedule.objects.select_for_update().select_related('template_card__column__board__owner','column__board').get(pk=pk)
    if not rule.enabled or rule.mode!='calendar' or not rule.next_at: return 0
    owner=rule.template_card.column.board.owner
    if not account_allowed(owner): return 0
    if rule.template_card.state in ['trash','archived']:
        rule.enabled=False;rule.next_at=None;rule.save();return 0
    count=0
    lead=timedelta(minutes=rule.template_card.reminder_minutes if rule.template_card.reminder_enabled else 0)
    while rule.next_at and rule.next_at-lead<=now and count<50:
        if rule.max_count and rule.generated_count>=rule.max_count: rule.next_at=None;break
        when=rule.next_at
        _,created=create_occurrence(rule,when)
        if created: rule.generated_count+=1;count+=1
        rule.next_at=next_run(rule,when)
    rule.save(update_fields=['next_at','generated_count'])
    return count


def tick(now=None,sync_google=True):
    now=now or timezone.now();token=secrets.token_hex(16)
    try:
        SchedulerLease.objects.get_or_create(key='personal',defaults={'expires_at':now})
        claimed=SchedulerLease.objects.filter(key='personal',expires_at__lte=now).update(token=token,expires_at=now+timedelta(minutes=5),heartbeat_at=now)
        if not claimed: return {'busy':True}
        created=0
        try:
            # Access verification must not wait for recurrence or calendar processing.
            from accounts.hiworks_access import refresh_directory
            refresh_directory()
            from .notices import publish_due,deliver_pending as deliver_notices
            publish_due(now)
            deliver_notices()
            from .models import WorkSeries
            from .work_series import process_work_rule
            for pk in WorkSeries.objects.filter(enabled=True,mode='calendar',next_at__lte=now).values_list('pk',flat=True):
                created+=process_work_rule(pk,now)
            for pk in PersonalSchedule.objects.filter(enabled=True,mode='calendar',next_at__lte=now+timedelta(days=1)).values_list('pk',flat=True):
                created+=process_rule(pk,now)
            for reminder in PersonalReminder.objects.filter(delivered_at__isnull=True,notify_at__lte=now,card__state='active').select_related('card__column__board__owner'):
                if account_allowed(reminder.card.column.board.owner):
                    PersonalReminder.objects.filter(pk=reminder.pk,delivered_at__isnull=True).update(delivered_at=now)
            if sync_google:
                from .google_calendar import sync_connection
                for connection in GoogleCalendarConnection.objects.exclude(status__in=['disconnected','reconnect']).select_related('user'):
                    if account_allowed(connection.user): sync_connection(connection)
                    SchedulerLease.objects.filter(key='personal',token=token).update(expires_at=timezone.now()+timedelta(minutes=5),heartbeat_at=timezone.now())
            from .hiworks import deliver_pending, sync_automatic_links
            sync_automatic_links()
            deliver_pending()
            from .assignment_mail import deliver_pending as deliver_assignment_mail
            deliver_assignment_mail()
            return {'created':created}
        finally:
            SchedulerLease.objects.filter(key='personal',token=token).update(expires_at=timezone.now(),heartbeat_at=timezone.now())
    except OperationalError:
        return {'busy':True}


def start():
    global _stop
    if not settings.WORKHUB_SCHEDULER_ENABLED or _stop is not None: return
    _stop=threading.Event()
    def run():
        while not _stop.wait(15):
            close_old_connections()
            try: tick(sync_google=False)
            except Exception: log.warning('Workhub scheduled work will retry on the next cycle.')
            finally: close_old_connections()
    threading.Thread(target=run,name='workhub-scheduler',daemon=True).start()
    def calendars():
        while not _stop.wait(45):
            close_old_connections()
            try:
                now=timezone.now();token=secrets.token_hex(16)
                SchedulerLease.objects.get_or_create(key='google',defaults={'expires_at':now})
                if not SchedulerLease.objects.filter(key='google',expires_at__lte=now).update(token=token,expires_at=now+timedelta(minutes=10)):continue
                try:
                    from .google_calendar import sync_connection
                    for connection in GoogleCalendarConnection.objects.exclude(status__in=['disconnected','reconnect']).select_related('user'):
                        if account_allowed(connection.user):sync_connection(connection)
                        SchedulerLease.objects.filter(key='google',token=token).update(expires_at=timezone.now()+timedelta(minutes=10))
                finally:SchedulerLease.objects.filter(key='google',token=token).update(expires_at=timezone.now())
            except Exception:log.warning('Calendar synchronization will retry on the next cycle.')
            finally:close_old_connections()
    threading.Thread(target=calendars,name='workhub-google-calendar',daemon=True).start()


def stop():
    if _stop is not None:_stop.set()
