from django.db.models import Q

def next_report_number(task,user):
    cached=getattr(task,'_prefetched_objects_cache',{}).get('events')
    if cached is not None:
        return sum(e.kind=='progress' and e.author_id==user.pk and e.snapshot.get('action','progress')=='progress' for e in cached)+1
    # Completion actions also used the progress event kind; they are not reports.
    return task.events.filter(kind='progress',author=user).filter(
        Q(snapshot__action='progress') | ~Q(snapshot__has_key='action')
    ).count()+1
