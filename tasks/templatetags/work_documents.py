from django import template
from tasks.documents import can_add, parent_kwargs
from tasks.models import WorkDocument
register=template.Library()


@register.inclusion_tag('tasks/documents.html',takes_context=True)
def work_documents(context,kind,obj):
    request=context['request']
    documents=WorkDocument.objects.filter(**parent_kwargs(kind,obj),archived_at__isnull=True)
    # Assigned work reuses original request references without copying files or widening project access.
    sources=[]
    if kind=='task':
        from tasks.permissions import visible_requests
        sources=WorkDocument.objects.filter(work_request__in=visible_requests(request.user).filter(task=obj),archived_at__isnull=True)
    return {'documents':documents,'source_documents':sources,'kind':kind,'parent':obj,
        'can_add_document':can_add(request.user,kind,obj),'request':request,'messages':context.get('messages',[])}
