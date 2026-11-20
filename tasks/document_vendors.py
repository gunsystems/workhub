"""Vendor catalog; disable never deletes folders or changes access rights."""
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import DocumentVendor,DocumentRollout
from .document_structure import VENDORS, VENDOR_BASES, validate_name
from .permission_inventory import scope
from .document_rollout import verify_groups
from .group_permission_api import GroupPermissionClient
from .audit import write


def catalog(site):
    rows=list(DocumentVendor.objects.filter(site_url=site))
    if rows:return rows
    from types import SimpleNamespace
    ready=DocumentRollout.objects.filter(site_url=site,state='done').exists()
    return [SimpleNamespace(pk=None,name=name,is_active=True,ready=ready) for name in VENDORS]


def active_names(site):
    rows=DocumentVendor.objects.filter(site_url=site)
    if not rows.exists():return list(VENDORS)
    return list(rows.filter(is_active=True,ready=True).values_list('name',flat=True))


def seed(site):
    for name in VENDORS:
        DocumentVendor.objects.get_or_create(site_url=site,normalized_name=name.casefold(),
            defaults={'name':name,'ready':True})


def queue_vendor(user,name,api=None):
    validate_name(name,limit=80)
    key,site=scope(user)
    api=api or GroupPermissionClient(user)
    # The new job's review must show fresh memberships, not the initial rollout's old snapshot.
    with transaction.atomic():
        run=DocumentRollout.objects.select_for_update().get(site_url=site,owner=user,scope_key=key)
        if run.state!='done' or run.pending or run.lease_token:
            raise ValidationError('먼저 진행 중인 문서함 적용을 완료하세요.')
        seed(site)
        if DocumentVendor.objects.filter(site_url=site,normalized_name=name.casefold()).exists():
            raise ValidationError('이미 등록된 벤더 폴더명입니다.')
        if DocumentVendor.objects.filter(site_url=site).count()>=100:
            raise ValidationError('벤더 목록은 100개까지 등록할 수 있습니다.')
        for group in run.data['groups'].values():
            group['members']=api.members(group['id'])
            from .document_permission_api import digest
            group['digest']=digest(group['members'])
        verify_groups(api,run.data)
        DocumentVendor.objects.create(site_url=site,name=name,ready=False)
        run.data['vendors']=list(DocumentVendor.objects.filter(site_url=site).values_list('name',flat=True))
        run.data.update(phase='vendor',phase_vendor=name)
        run.data['steps'].extend([['folder','documents/'+'/'.join(base+(name,))] for base in VENDOR_BASES])
        run.state='prepared';run.revision+=1;run.save()
        write('create','DocumentVendor',name,'벤더 폴더 생성 검토 준비',after={'벤더':name,'생성 위치':6},actor=user)
        return run


@transaction.atomic
def toggle(user,name,active):
    key,site=scope(user)
    run=DocumentRollout.objects.select_for_update().filter(site_url=site,owner=user,scope_key=key,state='done').first()
    if not run or run.pending or run.lease_token:
        raise ValidationError('문서함 초기 적용과 현재 관리자 연결을 확인하세요.')
    seed(site)
    row=DocumentVendor.objects.select_for_update().filter(site_url=site,normalized_name=name.casefold()).first()
    if not row:raise ValidationError('등록된 벤더를 다시 선택하세요.')
    old=row.is_active;row.is_active=bool(active);row.save(update_fields=['is_active'])
    write('update','DocumentVendor',row.pk,'벤더 선택 목록 변경',before={'활성':old},
        after={'벤더':row.name,'활성':row.is_active,'기존 폴더':'유지'},actor=user)
    return row
