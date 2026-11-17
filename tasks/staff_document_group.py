"""Consent-bound staff membership automation; no folder/tenant role changes."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.db import transaction,IntegrityError
from django.utils import timezone
from .models import Employee,HiworksDirectoryCache,OffboardingCase,DocumentStaffGroup,DocumentPermissionLease,PermissionReportName
from .document_permission_api import digest
from .permission_inventory import scope,person_identity,invalidate
from .sharepoint_probe import SharePointProbeError
from .audit import write


def roster():
    today=timezone.localdate()
    departed=set(OffboardingCase.objects.filter(state__in=['active','done'],departure_date__lt=today).values_list('employee_id',flat=True))
    result={}
    for e in Employee.objects.select_related('user','invitation','hiworkslink','employment'):
        email=(getattr(e.user,'email','') or getattr(getattr(e,'invitation',None),'email','') or getattr(getattr(e,'hiworkslink',None),'email','')).strip().casefold()
        end=getattr(getattr(e,'employment',None),'ended_on',None)
        active=e.is_active and (not e.user_id or e.user.is_active) and e.pk not in departed and (not end or end>=today)
        result[str(e.pk)]={'id':e.pk,'email':email,'name':e.name,'active':bool(active)}
    cache=HiworksDirectoryCache.objects.filter(key='access').first()
    fresh=bool(cache and cache.checked_at and timezone.now()-timedelta(minutes=5)<=cache.checked_at<=timezone.now())
    directory={str(r['remote_id']):{'email':str(r['email']).casefold(),'name':r['name']} for r in cache.rows if r.get('remote_id') and r.get('email') and r.get('name')} if cache else {}
    return {'staff':result,'directory':directory,'fresh':fresh,'day':today.isoformat()}


def company_email(value):
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:validate_email(value)
    except ValidationError:return False
    return value.endswith('@'+settings.WORKHUB_EMAIL_DOMAIN)


def seed_bindings(api,group_id,source=None):
    """Existing members only; alternate logins are explicitly reviewed here."""
    source=source or roster();before=api.members(group_id);bindings={}
    aliases={r.principal_id:r for r in PermissionReportName.objects.filter(site_url=api.config.site_url)}
    for member in before:
        if member['type']!=1:raise SharePointProbeError('중첩 그룹은 전 직원 자동 관리로 전환할 수 없습니다.')
        identity=person_identity(member);email=member['email']
        alias=aliases.get(member['id'])
        if alias and alias.identity==identity:email=alias.directory_email.casefold()
        matches=[r for r in source['staff'].values() if r['email'] and r['email']==email]
        if len(matches)>1:raise SharePointProbeError('직원 이메일이 중복되어 자동 관리를 시작할 수 없습니다.')
        binding={'principal':member['id'],'identity':identity,'email':email,'name':member['name']}
        if matches:
            employee=matches[0];key='employee:'+str(employee['id']);binding['name']=employee['name']
        else:
            candidates=[(key,r) for key,r in source['directory'].items() if r['email']==email]
            if not source['fresh'] or len(candidates)!=1:raise SharePointProbeError('기존 구성원의 직원 등록 정보를 확인한 뒤 다시 검토하세요.')
            key='directory:'+candidates[0][0];binding['name']=candidates[0][1]['name']
        if not identity or key in bindings:raise SharePointProbeError('직원 계정 연결이 중복되거나 불명확합니다.')
        bindings[key]=binding
    return before,bindings


def desired_sources(bindings,source):
    result={};by_email={}
    for key,e in source['staff'].items():
        if e['email']:by_email.setdefault(e['email'],[]).append(key)
        if e['active']:result['employee:'+key]=e
    for key,b in bindings.items():
        if not key.startswith('directory:'):continue
        if b['email'] in by_email:continue # Local employment/suspension overrides legacy directory membership.
        row=source['directory'].get(key.split(':',1)[1])
        if not source['fresh']:
            result[key]={**b,'hold':True,'active':True}
        elif row and row['email']==b['email']:
            result[key]={**row,'active':True}
    if any(len(keys)>1 for keys in by_email.values()):raise SharePointProbeError('직원 이메일이 중복되어 동기화를 중단했습니다.')
    return result


def sync_members(api,policy,check=lambda:None):
    api.check_membership_group(policy.group.principal_id)
    if not int(api.call('effectivebasepermissions').get('Low',0)) & (1<<25):
        raise SharePointProbeError('자동 관리를 실행할 관리자 권한이 없습니다.')
    current=api.members(policy.group.principal_id);source=roster();source_key=digest(source)
    wanted=desired_sources(policy.bindings,source);bindings={};pending=[]
    by_email={r['email']:r for r in current if r['type']==1 and r['email']}
    by_id={r['id']:r for r in current}
    def guard():
        check()
        if digest(roster())!=source_key:raise SharePointProbeError('동기화 중 직원 정보가 변경되었습니다.')
    # Resolve exact company emails. Display-name links never authorize new entrants.
    for key,staff in wanted.items():
        previous=policy.bindings.get(key)
        if not previous and key.startswith('employee:'):
            previous=next((b for k,b in policy.bindings.items() if k.startswith('directory:') and b['email']==staff['email']),None)
        person=by_id.get(previous['principal']) if previous and previous['email']==staff['email'] else None
        if person and person_identity(person)!=previous['identity']:
            raise SharePointProbeError('Microsoft 계정 연결이 변경되어 확인이 필요합니다.')
        if staff.get('hold'):
            if person:bindings[key]=previous
            pending.append(staff.get('name','직원'));continue
        if previous and previous['email']==staff['email']:
            # A previously consented immutable site principal can be restored on reactivation.
            try:
                person=person or api.call(f"getuserbyid({int(previous['principal'])})?$select=Id,Email,LoginName,PrincipalType")
            except SharePointProbeError:
                pending.append(staff['name']);continue
            if 'Id' in person:
                person={'id':int(person['Id']),'email':person.get('Email','').casefold(),'login':person.get('LoginName',''),'type':int(person['PrincipalType'])}
            if person['type']!=1 or person_identity(person)!=previous['identity']:raise SharePointProbeError('계정 연결을 다시 확인하세요.')
        elif company_email(staff['email']):
            person=by_email.get(staff['email'])
            if not person:
                guard()
                try:
                    pid=api.principal(staff['email'])
                    raw=api.call(f'getuserbyid({pid})?$select=Id,Email,LoginName,PrincipalType')
                    person={'id':int(raw['Id']),'email':raw.get('Email','').casefold(),'login':raw.get('LoginName',''),'type':int(raw['PrincipalType'])}
                    emails={person['email'],person['login'].rsplit('|',1)[-1].casefold()}
                    if person['type']!=1 or staff['email'] not in emails:raise SharePointProbeError('계정 확인 필요')
                except SharePointProbeError:
                    pending.append(staff['name']);continue
        else:
            pending.append(staff['name']);continue
        bindings[key]={'principal':person['id'],'identity':person_identity(person),'email':staff['email'],'name':staff['name']}
    desired={b['principal'] for b in bindings.values()}
    if len(desired)!=len(bindings):raise SharePointProbeError('직원 계정 연결이 중복됩니다.')
    def mutate(pid,add):
        nonlocal current
        guard()
        if digest(api.members(policy.group.principal_id))!=digest(current):raise SharePointProbeError('외부에서 구성원이 변경되었습니다.')
        write('permission_started','DocumentStaffGroup',policy.pk,'전 직원 자동 구성원 변경 시작',after={'Microsoft 대상':pid,'작업':'추가' if add else '제외'},actor=policy.owner)
        if add:
            person=api.call(f'getuserbyid({pid})?$select=LoginName')
            api.call(f'sitegroups/getbyid({policy.group.principal_id})/users','POST',{'LoginName':person['LoginName']})
        else:
            api.call(f'sitegroups/getbyid({policy.group.principal_id})/users/removebyid({pid})','POST')
        after=api.members(policy.group.principal_id)
        expected=({r['id'] for r in current}|{pid}) if add else ({r['id'] for r in current}-{pid})
        if {r['id'] for r in after}!=expected:raise SharePointProbeError('자동 구성원 변경 결과를 확인하세요.')
        current=after
        write('update','DocumentStaffGroup',policy.pk,'전 직원 자동 구성원 '+('추가' if add else '제외'),after={'그룹':policy.group.name,'Microsoft 대상':pid},actor=policy.owner)
        invalidate(policy.group.site_url)
    # Remove departing/unregistered members before adding new staff; never touch other groups or ACLs.
    for pid in sorted({r['id'] for r in current}-desired):mutate(pid,False)
    for pid in sorted(desired-{r['id'] for r in current}):mutate(pid,True)
    guard()
    # Retain consented historical identity links for a later return/cancellation.
    # Only the active bindings above determine actual group membership.
    return {**policy.bindings,**bindings},len(pending)


def synchronize_due():
    now=timezone.now();policy=DocumentStaffGroup.objects.filter(enabled=True,next_at__lte=now,lease_until__lte=now).select_related('owner','group').order_by('next_at').first()
    if not policy:return False
    token=secrets.token_hex(16)
    if not DocumentStaffGroup.objects.filter(pk=policy.pk,enabled=True,lease_until__lte=now).update(lease_token=token,lease_until=now+timedelta(minutes=10)):return False
    fence=DocumentStaffGroup.objects.filter(pk=policy.pk,lease_token=token,enabled=True,revision=policy.revision)
    lease=None
    try:
        def check():
            policy.owner.refresh_from_db()
            if not policy.owner.is_active or not policy.owner.is_superuser or not fence.exists() or scope(policy.owner)[0]!=policy.connection_key:
                raise SharePointProbeError('자동 관리용 관리자 연결을 다시 확인하세요.')
        check()
        from .group_permission_api import GroupPermissionClient
        api=GroupPermissionClient(policy.owner)
        if api.config.site_url!=policy.group.site_url:raise SharePointProbeError('사이트 연결이 변경되었습니다.')
        key=digest(['document-permission-site',api.config.site_url])
        with transaction.atomic():
            DocumentPermissionLease.objects.filter(target=key,expires_at__lt=now).delete()
            lease=DocumentPermissionLease.objects.create(target=key,expires_at=now+timedelta(minutes=10))
        def guarded():
            check()
            if not DocumentPermissionLease.objects.filter(pk=lease.pk,expires_at__gt=timezone.now()).exists():raise SharePointProbeError('처리 잠금이 만료되었습니다.')
        bindings,pending=sync_members(api,policy,guarded)
        fence.update(bindings=bindings,pending=pending,last_checked_at=timezone.now(),next_at=timezone.now()+timedelta(minutes=5),error='계정 확인 대기 직원이 있습니다.' if pending else '')
        return True
    except Exception:
        if fence.filter(error='').exists():
            write('permission_attention','DocumentStaffGroup',policy.pk,'전 직원 자동 반영 확인 필요',after={'상태':'관리자 연결·직원 계정·외부 변경 확인 필요'},actor=policy.owner)
        fence.update(next_at=timezone.now()+timedelta(minutes=5),error='자동 반영 확인 필요 · 관리자 연결과 직원 계정을 확인하세요.')
        return False
    finally:
        if lease:DocumentPermissionLease.objects.filter(pk=lease.pk).delete()
        DocumentStaffGroup.objects.filter(pk=policy.pk,lease_token=token).update(lease_token='',lease_until=timezone.now())
