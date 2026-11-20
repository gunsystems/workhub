"""Guarded initial rollout, one journaled operation per administrator POST.

Never adopt a pre-existing standard folder or retry an uncertain mutation.
Legacy file operations, ACL writes and library edits are deliberately absent.
"""
import secrets
import uuid
from datetime import timedelta
from django.db import transaction, IntegrityError
from django.utils import timezone
from .audit import write
from .document_permission_api import digest
from .document_structure import POLICY_VERSION, COMMON_ROOT_GRANTS, fixed_folders
from .group_permission_api import GroupPermissionClient, entries
from .models import DocumentRollout, DocumentAccessGroup, Employee, DocumentPermissionLease
from .permission_inventory import scope, invalidate
from .shared_documents import GRAPH, segment
from .shared_document_http import graph_write
from .sharepoint_probe import SharePointProbeError

NEW_GROUPS = {'sales':'GS_영업', 'support':'GS_경영지원', 'executives':'GS_경영진'}
EXISTING_GROUPS = {'staff':'전 직원', 'technical':'팀 · 보안기술팀', 'admins':'문서함 관리자'}
CUSTOM_ROLE = 'Workhub 편집(삭제불가)'
DELETE_BITS = (1 << 3) | (1 << 7)


def acl_signature(state):
    # Limited Access is SharePoint-maintained traversal, not content access.
    return digest({'unique':state['unique'],'rows':sorted((r['id'],r['type'],
        sorted((int(rid),int(kind)) for rid,kind in r['roles'] if int(kind)!=1))
        for r in state['rows'] if not r['limited'])})


def role_catalog(api):
    return entries(api.call('roledefinitions?$select=Id,Name,RoleTypeKind,BasePermissions'))


def no_delete_permissions(role):
    raw=role['BasePermissions']
    low,high=int(raw['Low']),int(raw['High'])
    if low < 0 or high < 0 or low > 0xffffffff or high > 0xffffffff:
        raise SharePointProbeError('권한 수준의 비트 구성을 확인하세요.')
    return {'Low':str(low & ~DELETE_BITS), 'High':str(high)}


def steps():
    result=[('group',key) for key in NEW_GROUPS]
    result += [('executive_member','executives'),('role','no_delete'),
               ('library','common'),('bind_library','common'),('acl','common/')]
    for folder in fixed_folders():
        result.append(('folder',folder.display))
        if folder.unique: result.append(('acl',folder.display))
    return result


def prepare(user, api=None):
    api=api or GroupPermissionClient(user)
    key,site=scope(user)
    existing=DocumentRollout.objects.filter(site_url=site).first()
    if existing:
        if existing.owner_id != user.pk or existing.scope_key != key or existing.policy != POLICY_VERSION:
            raise SharePointProbeError('기존 실행 기록과 관리자 연결을 먼저 확인하세요.')
        return existing
    groups=api.groups();bindings={}
    for role,name in EXISTING_GROUPS.items():
        found=[g for g in groups if g['name']==name]
        if len(found)!=1: raise SharePointProbeError(name+' 그룹을 명확히 확인하지 못했습니다.')
        group=found[0]
        members=api.members(group['id'])
        bindings[role]={**group,'members':members,'digest':digest(members)}
    if int(api.identity['Id']) not in {p['id'] for p in bindings['admins']['members']}:
        raise SharePointProbeError('관리자 그룹에서 현재 Microsoft 계정을 확인하세요.')
    if not Employee.objects.filter(is_active=True,position='executive',
            user__is_active=True,user__email__iexact=api.connection.email).exists():
        raise SharePointProbeError('초기 경영진 구성을 위해 대표 본인의 Microsoft 관리 연결을 확인하세요.')
    if any(g['name'] in NEW_GROUPS.values() for g in groups):
        raise SharePointProbeError('표준용 그룹이 이미 있습니다. 소유·구성원을 검토한 뒤 연결해야 합니다.')
    documents=[l for l in api.docs.libraries if l['name'] in ('문서','Documents')]
    if len(documents)!=1 or any(l['name']=='전사공통' for l in api.docs.libraries):
        raise SharePointProbeError('문서 라이브러리 또는 새 전사공통 이름의 중복을 확인하세요.')
    drive=documents[0]['id']
    children=api.docs.children(drive)
    roots={f.path[0].casefold() for f in fixed_folders() if f.library=='documents'}
    if any(c['name'].casefold() in roots for c in children):
        raise SharePointProbeError('표준 폴더와 같은 기존 경로가 있습니다. 기존 자료 보호를 위해 자동 적용을 중단합니다.')
    api.bind(drive,'root');root_state=api.state()
    for row in root_state['rows']:
        if not row['limited'] and (row['type']!=8 or
                (row['id']!=bindings['admins']['id'] and api.members(row['id']))):
            raise SharePointProbeError('상위 문서함에 예정하지 않은 접근 권한이 있어 자동 생성을 중단합니다.')
    api.resource='';site_acl=acl_signature(api.snapshot())
    roles=role_catalog(api)
    base=next((r for r in roles if int(r['RoleTypeKind'])==3),None)
    if not base or any(r['Name']==CUSTOM_ROLE for r in roles):
        raise SharePointProbeError('기여 권한 또는 기존 삭제불가 권한 수준을 검토하세요.')
    role_ids={k:int(next(r['Id'] for r in roles if int(r['RoleTypeKind'])==v))
              for k,v in {'read':2,'write':3,'manage':5}.items()}
    data={'groups':bindings,'libraries':{'documents':drive},'owned':{},'results':[],
          'role_ids':role_ids,'role_baseline':roles,'no_delete':no_delete_permissions(base),
          'actor_email':api.connection.email,'steps':[list(s) for s in steps()],
          'document_acl':acl_signature(root_state),'site_acl':site_acl}
    run=DocumentRollout.objects.create(site_url=site,owner=user,scope_key=key,policy=POLICY_VERSION,data=data)
    write('create','DocumentRollout',run.pk,'표준 문서함 적용 검토 준비',after={'정책':POLICY_VERSION,'단계':len(steps())},actor=user)
    return run


def verify_groups(api, data):
    catalog={g['id']:g for g in api.groups()}
    for group in data['groups'].values():
        if (group['id'] not in catalog or catalog[group['id']]['name']!=group['name'] or
                digest(api.members(group['id']))!=group['digest']):
            raise SharePointProbeError('그룹 구성원이 검토 이후 변경되었습니다. 현재 구성을 다시 확인하세요.')


def bind_owned(api,data,key):
    owned=data['owned'].get(key)
    if not owned: raise SharePointProbeError('직접 생성한 폴더의 실행 기록이 없습니다.')
    row=api.bind(owned['drive'],owned['item'])
    if owned['item']!='root' and (row['name']!=owned['name'] or
            (row.get('parentReference') or {}).get('id')!=owned['parent']):
        raise SharePointProbeError('생성한 폴더의 이름·위치가 변경되었습니다.')
    return row


def set_acl(api,data,key):
    bind_owned(api,data,key)
    verify_groups(api,data)
    grants=COMMON_ROOT_GRANTS if key=='common/' else next(f.grants for f in fixed_folders() if f.display==key)
    if not grants: raise SharePointProbeError('★가 아닌 위치의 상속은 변경하지 않습니다.')
    roles=role_catalog(api)
    custom=next((r for r in roles if int(r['Id'])==data['role_ids']['write_no_delete']),None)
    if not custom or custom['BasePermissions']!=data['no_delete']:
        raise SharePointProbeError('삭제불가 권한 수준이 변경되었습니다.')
    wanted={(int(data['groups'][g]['id']),int(data['role_ids'][r])) for g,r in grants}
    before=api.state()
    admin=int(data['groups']['admins']['id'])
    # No administrator or directly granted individual may be silently removed.
    if any(not r['limited'] and (r['type']!=8 or (r['protected'] and r['id']!=admin)) for r in before['rows']):
        raise SharePointProbeError('새 폴더의 관리자·개인 권한 구성이 예상과 다릅니다.')
    for row in before['rows']:
        if not row['limited'] and row['id'] not in {p for p,_ in wanted} and api.members(row['id']):
            raise SharePointProbeError('예정에 없는 구성원 접근이 있어 자동 권한 적용을 중단합니다.')
    if not before['unique']:
        before=api.change(before,'break',[],lambda *a:None)
    current=before
    actual={(r['id'],int(rid)) for r in current['rows'] for rid,kind in r['roles'] if kind!=1}
    for pid,rid in sorted(wanted-actual):
        current=api.mutate(current,f'/roleassignments/addroleassignment(principalid={pid},roledefid={rid})',lambda *a:None)
    for pid,rid in sorted(actual-wanted):
        if pid==admin: raise SharePointProbeError('관리자 권한은 자동 제거하지 않습니다.')
        current=api.mutate(current,f'/roleassignments/removeroleassignment(principalid={pid},roledefid={rid})',lambda *a:None)
    final={(r['id'],int(rid)) for r in current['rows'] for rid,kind in r['roles'] if kind!=1}
    if final!=wanted or not current['unique']:
        raise SharePointProbeError('권한 적용 결과가 정책과 일치하지 않습니다.')


def perform(api,run,operation,guard=lambda:None):
    data=run.data;action,key=operation
    guard()
    if action=='group':
        if any(g['name']==NEW_GROUPS[key] for g in api.groups()):
            raise SharePointProbeError('같은 이름의 그룹이 있어 새로 생성하지 않습니다.')
        gid=api.create_group(NEW_GROUPS[key]);members=api.members(gid)
        if members: raise SharePointProbeError('새 그룹의 예상치 못한 구성원을 확인하세요.')
        data['groups'][key]={'id':gid,'name':NEW_GROUPS[key],'members':members,'digest':digest(members)}
        DocumentAccessGroup.objects.create(site_url=run.site_url,principal_id=gid,name=NEW_GROUPS[key])
    elif action=='executive_member':
        group=data['groups']['executives'];members=api.members(group['id'])
        if members: raise SharePointProbeError('빈 경영진 그룹 상태를 다시 확인하세요.')
        members=api.group_change(group['id'],members,[data['actor_email']],[],lambda *a:None)
        group.update(members=members,digest=digest(members))
    elif action=='role':
        if any(r['Name']==CUSTOM_ROLE for r in role_catalog(api)):
            raise SharePointProbeError('같은 이름의 권한 수준이 이미 있습니다.')
        api.call('roledefinitions','POST',{'Name':CUSTOM_ROLE,'Description':'파일과 버전 삭제를 제외한 기여 권한',
            'BasePermissions':data['no_delete']})
        created=[r for r in role_catalog(api) if r['Name']==CUSTOM_ROLE]
        if len(created)!=1 or created[0]['BasePermissions']!=data['no_delete']:
            raise SharePointProbeError('삭제불가 권한 생성 결과를 확인하세요.')
        data['role_ids']['write_no_delete']=int(created[0]['Id'])
    elif action=='library':
        if any(l['name']=='전사공통' for l in api.docs.libraries):
            raise SharePointProbeError('새 전사공통과 같은 문서함이 이미 있습니다.')
        api.resource=''
        if acl_signature(api.snapshot())!=data['site_acl']:
            raise SharePointProbeError('사이트 기본 권한이 검토 이후 변경되었습니다.')
        verify_groups(api,data)
        row=api.call('lists','POST',{'Title':'전사공통','BaseTemplate':101,
            'Description':'Workhub 표준 회사 운영 자료','EnableVersioning':True,
            'MajorVersionLimit':500,'EnableMinorVersions':False,'ForceCheckout':False})
        data['common_list']=str(uuid.UUID(row['Id']))
    elif action=='bind_library':
        choices=[l for l in api.docs.libraries if l['name']=='전사공통']
        if len(choices)!=1: raise SharePointProbeError('새 문서함의 연결이 아직 완료되지 않았습니다.')
        drive=choices[0]['id'];api.bind(drive,'root')
        if api.list_id!=data['common_list']: raise SharePointProbeError('생성한 문서함의 식별자가 다릅니다.')
        data['libraries']['common']=drive
        data['owned']['common/']={'drive':drive,'item':'root','name':'전사공통','parent':''}
    elif action=='folder':
        spec=next(f for f in fixed_folders() if f.display==key)
        drive=data['libraries'][spec.library]
        parent_key=spec.library+'/'+'/'.join(spec.path[:-1])
        parent=data['owned'].get(parent_key)
        if len(spec.path)>1 or spec.library=='common':
            if not parent: raise SharePointProbeError('상위 폴더의 생성 기록이 없습니다.')
            bind_owned(api,data,parent_key)
        else:
            api.bind(drive,'root')
            if acl_signature(api.state())!=data['document_acl']:
                raise SharePointProbeError('상위 문서함 권한이 검토 이후 변경되었습니다.')
        parent_id=parent['item'] if parent else 'root'
        children=api.docs.children(drive,'' if parent_id=='root' else parent_id)
        if any(c['name'].casefold()==spec.path[-1].casefold() for c in children):
            raise SharePointProbeError('같은 이름의 기존 경로를 발견했습니다. 덮어쓰지 않고 중단합니다.')
        resource=f'{GRAPH}/drives/{segment(drive)}/'+('root' if parent_id=='root' else f'items/{segment(parent_id)}')+'/children'
        guard()
        row=graph_write(resource,api.docs.token,'POST',{'name':spec.path[-1],'folder':{},'@microsoft.graph.conflictBehavior':'fail'})
        api.docs.validate_item(row,drive)
        if 'folder' not in row or row['name']!=spec.path[-1]: raise SharePointProbeError('폴더 생성 결과를 확인하세요.')
        data['owned'][key]={'drive':drive,'item':row['id'],'name':row['name'],'parent':row['parentReference']['id']}
    elif action=='acl': set_acl(api,data,key)
    else: raise SharePointProbeError('허용되지 않은 적용 단계입니다.')
    data['results'].append({'action':action,'target':key,'at':timezone.now().isoformat()})


def advance(user,run_id,revision,api=None):
    key,site=scope(user);token=secrets.token_hex(16)
    with transaction.atomic():
        run=DocumentRollout.objects.select_for_update().get(pk=run_id,owner=user,site_url=site)
        if run.scope_key!=key or run.policy!=POLICY_VERSION or run.revision!=revision:
            raise SharePointProbeError('관리자 연결 또는 적용 단계가 변경되었습니다.')
        if run.state not in ('prepared','running') or run.pending or run.lease_until>timezone.now():
            raise SharePointProbeError('실행 중이거나 결과 확인이 필요한 단계입니다. 중복 실행하지 않습니다.')
        try:
            with transaction.atomic():
                target=digest(['document-permission-site',site])
                DocumentPermissionLease.objects.filter(target=target,expires_at__lt=timezone.now()).delete()
                lease=DocumentPermissionLease.objects.create(target=target,expires_at=timezone.now()+timedelta(minutes=5))
        except IntegrityError:
            raise SharePointProbeError('다른 문서 권한 변경이 진행 중입니다. 종료 후 다음 단계를 실행하세요.') from None
        operation=run.data['steps'][run.cursor]
        run.state='running';run.pending=' / '.join(operation)
        run.lease_token=token;run.lease_until=timezone.now()+timedelta(minutes=5)
        run.save()
    original_call=None
    try:
        api=api or GroupPermissionClient(user)
        def guard():
            user.refresh_from_db()
            if (not user.is_active or not user.is_superuser or scope(user)[0]!=key or
                    not DocumentPermissionLease.objects.filter(pk=lease.pk,expires_at__gt=timezone.now()).exists() or
                    not DocumentRollout.objects.filter(pk=run.pk,lease_token=token).exists()):
                raise SharePointProbeError('관리자 연결 또는 실행 잠금이 만료되어 중단했습니다.')
        original_call=api.call
        def guarded_call(suffix,method='GET',data=None):
            if method!='GET':guard()
            return original_call(suffix,method,data)
        api.call=guarded_call
        perform(api,run,operation,guard)
        write('permission_apply' if operation[0]=='acl' else 'create','DocumentRollout',run.pk,
              '표준 문서함 단계 적용',after={'작업':operation[0],'대상':operation[1]},actor=user)
        run.cursor+=1;run.revision+=1;run.pending='';run.error=''
        run.state='done' if run.cursor==len(run.data['steps']) else 'running'
        run.lease_until=timezone.now();run.lease_token='';run.save()
        invalidate(site)
        return run
    except Exception as exc:
        # Journal stays pending. An unknown write outcome must not be retried.
        DocumentRollout.objects.filter(pk=run.pk,lease_token=token).update(state='attention',
            error=(str(exc)[:190] if isinstance(exc,SharePointProbeError) else '현재 단계의 외부 적용 결과를 확인해야 합니다.')+' 자동 재실행하지 않습니다.',
            data=run.data,lease_until=timezone.now(),lease_token='')
        write('permission_attention','DocumentRollout',run.pk,'표준 문서함 적용 결과 확인 필요',
              after={'대상':operation[1],'작업':operation[0]},actor=user)
        raise
    finally:
        if original_call is not None:api.call=original_call
        DocumentPermissionLease.objects.filter(pk=lease.pk).delete()
