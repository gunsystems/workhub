"""Fill automatic relationships without arbitrarily choosing among equal leaders."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import pre_save,post_save,post_delete,post_migrate
from .models import Employee

FIELDS=['department_id','position','is_active','supervisor_id','supervisor_auto','user_id']


@transaction.atomic
def reconcile(department_ids=None):
    from .audit import write
    rows={r['id']:r for r in Employee.objects.values('id','name',*FIELDS,'user__is_active')}
    departments=set(department_ids) if department_ids is not None else {r['department_id'] for r in rows.values()}
    eligible=lambda r:r['is_active'] and (r['user_id'] is None or r['user__is_active'])
    def unique(department,position):
        people=[r['id'] for r in rows.values() if r['department_id']==department and r['position']==position and eligible(r)]
        return people[0] if len(people)==1 else None
    def cycles(owner,candidate):
        seen={owner}
        while candidate:
            if candidate in seen:return True
            seen.add(candidate);candidate=rows.get(candidate,{}).get('supervisor_id')
        return False
    changed=0
    rank={'executive':0,'head':1,'leader':2,'member':3}
    for person in sorted(rows.values(),key=lambda r:(rank.get(r['position'],4),r['id'])):
        if person['department_id'] not in departments or not person['supervisor_auto'] or not eligible(person):continue
        department=person['department_id'];candidate=None
        if person['position']=='member':
            leaders=[r for r in rows.values() if r['department_id']==department and r['position']=='leader' and eligible(r)]
            candidate=unique(department,'leader') if leaders else unique(department,'head')
        elif person['position']=='leader':candidate=unique(department,'head')
        if candidate==person['id'] or cycles(person['id'],candidate):candidate=None
        previous=person['supervisor_id']
        if previous==candidate:continue
        updated=Employee.objects.filter(pk=person['id'],supervisor_auto=True,supervisor_id=previous,
            department_id=department,position=person['position'],is_active=True).update(supervisor_id=candidate)
        if updated:
            person['supervisor_id']=candidate;changed+=1
            write('auto_supervisor','Employee',person['id'],person['name'],
                before={'supervisor_id':previous},after={'supervisor_id':candidate,'처리 방식':'부서·직책 기준 자동 연결'})
    return changed


def before(sender,instance,**kwargs):
    if sender._meta.apps is not apps:return
    if sender is Employee:
        instance._supervisor_previous=Employee.objects.filter(pk=instance.pk).values(*FIELDS).first() if instance.pk else None
    elif sender._meta.concrete_model is get_user_model():
        instance._supervisor_old_active=get_user_model().objects.filter(pk=instance.pk).values_list('is_active',flat=True).first() if instance.pk else None


def after(sender,instance,raw=False,**kwargs):
    if raw or sender._meta.apps is not apps:return
    if sender is Employee:
        old=getattr(instance,'_supervisor_previous',None)
        current={field:getattr(instance,field) for field in FIELDS}
        if old is None or old!=current:
            reconcile({instance.department_id}|({old['department_id']} if old else set()))
            saved=Employee.objects.filter(pk=instance.pk).values('supervisor_id','supervisor_auto').first()
            if saved:
                instance.supervisor_id=saved['supervisor_id'];instance.supervisor_auto=saved['supervisor_auto']
                instance._state.fields_cache.pop('supervisor',None)
    elif sender._meta.concrete_model is get_user_model() and getattr(instance,'_supervisor_old_active',None)!=instance.is_active:
        reconcile(set(Employee.objects.filter(user_id=instance.pk).values_list('department_id',flat=True)))


def deleted(sender,instance,**kwargs):
    if sender is Employee and sender._meta.apps is apps:reconcile({instance.department_id})


def migrated(sender,**kwargs):
    if sender.name=='tasks':reconcile()


def register():
    pre_save.connect(before,dispatch_uid='supervisor_before',weak=False)
    post_save.connect(after,dispatch_uid='supervisor_after',weak=False)
    post_delete.connect(deleted,sender=Employee,dispatch_uid='supervisor_deleted',weak=False)
    post_migrate.connect(migrated,dispatch_uid='supervisor_migrated',weak=False)
