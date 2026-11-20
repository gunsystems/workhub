"""Journaled lifecycle for newly provisioned projects, never legacy folders."""
import copy
import secrets
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from .models import DocumentProject, DocumentRollout, DocumentPermissionLease, DocumentAccessGroup, SharedDocument
from .document_project_api import ProjectAPI
from .document_structure import project_name, project_folders, PROJECT_CHILDREN, ARCHIVE
from .document_vendors import active_names
from .document_rollout import acl_signature, verify_groups
from .document_permission_api import digest
from .permission_inventory import scope, invalidate
from .shared_documents import GRAPH, segment
from .shared_document_http import graph_write
from .sharepoint_probe import SharePointProbeError
from .audit import write

CREATE_STEPS=['group','members','root','root_acl',*['child:'+c for c in PROJECT_CHILDREN],'finish_create']
CLOSE_STEPS=['freeze','year','move','poll','verify_move','sales_acl','technical_acl','archive_acl','finish_close']
LABELS={'group':'프로젝트 참여 그룹 생성','members':'참여자 연결','root':'프로젝트 폴더 생성',
    'root_acl':'프로젝트 접근 권한 적용','finish_create':'프로젝트 생성 확인','freeze':'편집 종료·자료 확인',
    'year':'보관 연도 확인','move':'버전 이력을 포함해 이관 요청','poll':'Microsoft 이관 진행 확인',
    'verify_move':'이관 자료·버전 검증','sales_acl':'영업 자료 열람 설정','technical_acl':'기술 자료 열람 설정',
    'archive_acl':'완료 프로젝트 열람 설정','finish_close':'업무 연결 갱신·종료 완료'}


def plan_labels(row):
    return [LABELS.get(s,s.replace('child:','하위 폴더 생성 · ')) for s in row.data['steps']]


def require_admin(user):
    if not user.is_active or not user.is_superuser:raise PermissionDenied


def fresh_groups(api,data):
    for group in data['groups'].values():
        group['members']=api.members(group['id']);group['digest']=digest(group['members'])
    verify_groups(api,data)
    if int(api.identity['Id']) not in {p['id'] for p in data['groups']['admins']['members']}:
        raise SharePointProbeError('문서함 관리자 연결을 확인하세요.')


def bind(api,record):
    if hasattr(api.docs,'_items'):api.docs._items.clear()
    row=api.bind(record['drive'],record['item'])
    if record['item']!='root' and (row['name']!=record['name'] or row['parentReference']['id']!=record['parent']):
        raise SharePointProbeError('프로젝트 폴더의 이름·위치가 변경되었습니다.')
    return row


def admin_parent(api,data,drive,item):
    api.bind(drive,item);state=api.state()
    admin=data['groups']['admins']['id']
    if any(not r['limited'] and (r['type']!=8 or (r['id']!=admin and api.members(r['id']))) for r in state['rows']):
        raise SharePointProbeError('생성·이관 상위 위치에 예상하지 않은 접근 권한이 있습니다.')
    return state


def prepare(user,project,values,participants,api=None):
    require_admin(user);key,site=scope(user);api=api or ProjectAPI(user)
    # Lock policy binding against another vendor/initial job preparation.
    initial=DocumentRollout.objects.get(site_url=site,owner=user,scope_key=key,state='done')
    if initial.pending or initial.lease_token:raise ValidationError('문서함 적용 작업을 먼저 완료하세요.')
    vendors=active_names(site)
    name=project_name(values['year'],customer=values.get('customer',''),vendor=values.get('vendor',''),
        kind=values.get('kind',''),internal=values.get('internal',''),vendors=vendors)
    if DocumentProject.objects.filter(project=project).exists() or DocumentProject.objects.filter(site_url=site,normalized_name=name.casefold()).exists():
        raise ValidationError('이미 연결된 프로젝트 또는 같은 이름의 폴더가 있습니다.')
    people=list(participants)
    if not people or project.owner_id not in {p.pk for p in people}:
        raise ValidationError('총괄 담당자를 참여자에 포함하세요.')
    if any(not p.is_active or not p.user_id or not p.user.is_active or not p.user.email for p in people):
        raise ValidationError('사용 가능한 직원 계정만 참여할 수 있습니다.')
    choices=[l for l in api.docs.libraries if l['name']=='프로젝트 공동작업']
    if len(choices)!=1:raise SharePointProbeError('프로젝트 공동작업 문서함을 확인하세요.')
    drive=choices[0]['id'];data=copy.deepcopy(initial.data)
    data={k:data[k] for k in ('groups','role_ids','owned','libraries')}
    fresh_groups(api,data);admin_parent(api,data,drive,'root')
    if any(c['name'].casefold()==name.casefold() for c in api.docs.children(drive)):
        raise ValidationError('같은 이름의 기존 폴더는 연결하거나 덮어쓰지 않습니다.')
    data.update(project_drive=drive,steps=CREATE_STEPS,records={},results=[])
    data['group_name']='프로젝트 · '+name
    if any(g['name']==data['group_name'] for g in api.groups()):
        raise ValidationError('같은 이름의 프로젝트 그룹이 있습니다.')
    metadata={**values,'participants':[{'id':p.pk,'name':p.name,'email':p.user.email} for p in people]}
    # No provider IO inside a SQLite read transaction: a background sync can
    # otherwise invalidate the snapshot before the first local write.
    with transaction.atomic():
        row=DocumentProject.objects.create(project=project,site_url=site,name=name,normalized_name=name.casefold(),
            creator=user,administrator=user,scope_key=key,metadata=metadata,data=data)
        row.participants.set(people)
        write('create','DocumentProject',row.pk,'프로젝트 폴더 생성 검토',after={'폴더':name,'참여자':[p.name for p in people]},actor=user)
    return row


def create_folder(api,drive,parent,name,guard):
    if any(c['name'].casefold()==name.casefold() for c in api.docs.children(drive,'' if parent=='root' else parent)):
        raise SharePointProbeError('같은 이름의 기존 폴더를 발견해 중단했습니다.')
    url=f'{GRAPH}/drives/{segment(drive)}/'+('root' if parent=='root' else f'items/{segment(parent)}')+'/children'
    guard();row=graph_write(url,api.docs.token,'POST',{'name':name,'folder':{},'@microsoft.graph.conflictBehavior':'fail'})
    api.docs.validate_item(row,drive)
    if row['name']!=name or 'folder' not in row:raise SharePointProbeError('폴더 생성 결과를 확인하세요.')
    return {'drive':drive,'item':row['id'],'name':name,'parent':row['parentReference']['id']}


def acl(api,data,record,wanted_roles,*,allow_remove=()):
    bind(api,record);verify_groups(api,data)
    wanted={(data['groups'][g]['id'],data['role_ids'][r]) for g,r in wanted_roles}
    state=api.state();admin=data['groups']['admins']['id']
    for r in state['rows']:
        if r['limited']:continue
        if r['type']!=8 or (r['protected'] and r['id']!=admin):
            raise SharePointProbeError('예정하지 않은 개인·관리 권한이 있어 중단합니다.')
        if r['id'] not in {p for p,_ in wanted} and r['id'] not in allow_remove and api.members(r['id']):
            raise SharePointProbeError('예정에 없는 접근 대상을 자동 제거하지 않습니다.')
    if not state['unique']:state=api.change(state,'break',[],lambda *a:None)
    actual={(r['id'],int(rid)) for r in state['rows'] for rid,k in r['roles'] if k!=1}
    for pid,rid in sorted(wanted-actual):
        state=api.mutate(state,f'/roleassignments/addroleassignment(principalid={pid},roledefid={rid})',lambda *a:None)
    for pid,rid in sorted(actual-wanted):
        if pid==admin:raise SharePointProbeError('기존 관리자 권한은 유지해야 합니다.')
        state=api.mutate(state,f'/roleassignments/removeroleassignment(principalid={pid},roledefid={rid})',lambda *a:None)
    if {(r['id'],int(rid)) for r in state['rows'] for rid,k in r['roles'] if k!=1}!=wanted:
        raise SharePointProbeError('프로젝트 권한 검증에 실패했습니다.')


def check_acl(api,data,record,grants):
    bind(api,record);state=api.state()
    wanted={(data['groups'][g]['id'],data['role_ids'][r]) for g,r in grants}
    actual={(r['id'],int(rid)) for r in state['rows'] for rid,k in r['roles'] if k!=1}
    if not state['unique'] or actual!=wanted:
        raise SharePointProbeError('최종 프로젝트 접근 권한이 예정과 다릅니다.')


def manifest(api,root,*,archived=False):
    """Bounded fresh tree; refuse unknown unique ACLs and incomplete version lists."""
    result={};queue=[('',root['item'])];drive=root['drive']
    while queue:
        path,item=queue.pop(0)
        if len(result)>=200:raise SharePointProbeError('자료가 200개를 넘어 별도 이관 점검이 필요합니다.')
        raw=api.graph(drive,item,'?$select=id,name,file,folder,size,eTag,webUrl,parentReference')
        api.docs.validate_item(raw,drive)
        api.bind(drive,item);state=api.state()
        permitted_unique={'','01_영업','02_기술'} if archived else {''}
        if state['unique'] and path not in permitted_unique:
            raise SharePointProbeError('하위 자료에 별도 권한이 있어 자동 이관하지 않습니다.')
        entry={'item':item,'name':raw['name'],'parent':raw['parentReference']['id'],'folder':'folder' in raw}
        if entry['folder']:
            for child in api.docs.children(drive,item):
                childpath=path+'/'+child['name'] if path else child['name']
                if childpath in result or any(p==childpath for p,_ in queue):raise SharePointProbeError('중복된 경로입니다.')
                queue.append((childpath,child['id']))
        else:
            entry.update(size=raw['size'],etag=raw.get('eTag',''),hashes=raw.get('file',{}).get('hashes',{}),
                         versions=api.versions(drive,item))
            if not entry['versions'] or not entry['etag']:raise SharePointProbeError('파일 버전 상태를 확인하지 못했습니다.')
        result[path]=entry
    if not set(PROJECT_CHILDREN).issubset(result):raise SharePointProbeError('표준 하위 폴더 네 곳을 확인하세요.')
    return result


def content_signature(tree):
    return {p:{k:v for k,v in r.items() if k in ('folder','size','versions','hashes')} for p,r in tree.items()}


def archive_matches(before,after):
    """Compare retained content/history, not provider-local identity decoration."""
    from django.utils.dateparse import parse_datetime
    from datetime import timezone as utc
    def version_signature(value):
        stamp=value.get('modified')
        if stamp:
            parsed=parse_datetime(stamp)
            if parsed and parsed.tzinfo:stamp=parsed.astimezone(utc.utc).isoformat()
        actor=value.get('actor') or {}
        # IdentitySet IDs are provider-local decorations; names/times in the
        # actual version history are preserved and never used for authorization.
        names=tuple((kind,str((actor.get(kind) or {}).get('displayName','')).strip().casefold())
                    for kind in ('user','application') if (actor.get(kind) or {}).get('displayName'))
        return (str(value['id']),value['size'],stamp,names)
    if set(before)!=set(after):return False
    for path,old in before.items():
        new=after[path]
        if old['folder']!=new['folder']:return False
        if old['folder']:continue
        if old['size']!=new['size']:return False
        required={version_signature(v) for v in old['versions']}
        actual={version_signature(v) for v in new['versions']}
        if not required.issubset(actual):return False
        known={k:v for k,v in old.get('hashes',{}).items() if v}
        current={k:v for k,v in new.get('hashes',{}).items() if v}
        shared=set(known)&set(current)
        if known and not shared:return False
        if any(known[k]!=current[k] for k in shared):return False
    return True


def prepare_close(user,pk,api=None):
    require_admin(user);key,site=scope(user);api=api or ProjectAPI(user)
    row=DocumentProject.objects.get(pk=pk,site_url=site,administrator=user,scope_key=key,state='active')
    data=row.data;fresh_groups(api,data)
    root=bind(api,data['records']['root'])
    # Only the exact project group and known policy roles may be changed.
    expected={(data['groups'][g]['id'],data['role_ids'][r]) for g,r in project_folders(row.name)[0].grants}
    current={(r['id'],int(rid)) for r in api.state()['rows'] for rid,k in r['roles'] if k!=1}
    if current!=expected:raise SharePointProbeError('현재 프로젝트 접근 대상이 변경되었습니다. 먼저 권한을 검토하세요.')
    data['before']=manifest(api,data['records']['root'])
    archive=data['owned'].get('documents/'+ARCHIVE)
    if not archive:raise SharePointProbeError('직접 생성한 완료프로젝트 영역을 확인하지 못했습니다.')
    bind(api,archive);admin_parent(api,data,archive['drive'],archive['item'])
    year=str(timezone.localdate().year)
    found=[c for c in api.docs.children(archive['drive'],archive['item']) if c['name']==year]
    if found:
        known=DocumentProject.objects.filter(site_url=site).exclude(pk=row.pk)
        owned_year=next((p.data.get('year_record') for p in known if p.data.get('year_record',{}).get('item')==found[0]['id']),None)
        if not owned_year:raise SharePointProbeError('기존 연도 폴더는 별도 확인 없이 사용하지 않습니다.')
        bind(api,owned_year);admin_parent(api,data,owned_year['drive'],owned_year['item'])
        if any(c['name'].casefold()==row.name.casefold() for c in api.docs.children(archive['drive'],found[0]['id'])):
            raise SharePointProbeError('같은 이름의 보관 폴더가 있어 이관하지 않습니다.')
        data['year_record']=owned_year
    data.update(archive_parent=archive,year=year,steps=CLOSE_STEPS,source_root=data['records']['root'])
    changed=DocumentProject.objects.filter(pk=pk,state='active',revision=row.revision,scope_key=key).update(
        data=data,phase='close',state='prepared',cursor=0,revision=row.revision+1)
    if changed!=1:raise SharePointProbeError('종료 검토 중 프로젝트 상태가 변경되었습니다.')
    row.refresh_from_db()
    write('update','DocumentProject',row.pk,'프로젝트 종료·보관 검토',after={'폴더':row.name,'보관 연도':year},actor=user)
    return row


def perform(api,row,step,guard):
    data=row.data;records=data['records'];drive=data['project_drive']
    if step=='group':
        if any(g['name']==data['group_name'] for g in api.groups()):raise SharePointProbeError('같은 이름의 그룹이 있습니다.')
        gid=api.create_group(data['group_name']);members=api.members(gid)
        if members:raise SharePointProbeError('새 프로젝트 그룹의 구성을 확인하세요.')
        data['groups']['participants']={'id':gid,'name':data['group_name'],'members':[],'digest':digest([])}
        DocumentAccessGroup.objects.create(site_url=row.site_url,principal_id=gid,name=data['group_name'])
    elif step=='members':
        from .models import Employee
        planned=row.metadata['participants']
        for p in planned:
            if not Employee.objects.filter(pk=p['id'],is_active=True,user__is_active=True,user__email__iexact=p['email']).exists():
                raise SharePointProbeError('참여 직원의 계정 상태가 변경되었습니다.')
        group=data['groups']['participants']
        if api.members(group['id']):raise SharePointProbeError('새 참여 그룹이 변경되었습니다.')
        members=api.group_change(group['id'],[],[p['email'] for p in planned],[],lambda *a:None)
        if len(members)!=len(planned):raise SharePointProbeError('참여자 수가 일치하지 않습니다.')
        group.update(members=members,digest=digest(members))
    elif step=='root':
        admin_parent(api,data,drive,'root');records['root']=create_folder(api,drive,'root',row.name,guard)
    elif step=='root_acl':acl(api,data,records['root'],project_folders(row.name)[0].grants)
    elif step.startswith('child:'):
        bind(api,records['root']);child=step.split(':',1)[1]
        if child not in PROJECT_CHILDREN:raise SharePointProbeError('허용되지 않은 하위 폴더입니다.')
        records[child]=create_folder(api,drive,records['root']['item'],child,guard)
    elif step=='finish_create':
        verify_groups(api,data);check_acl(api,data,records['root'],project_folders(row.name)[0].grants)
        tree=manifest(api,records['root'])
        if set(tree)!={'',*PROJECT_CHILDREN}:raise SharePointProbeError('새 프로젝트 구조가 예상과 다릅니다.')
        row.state='active'
    elif step=='freeze':
        root=bind(api,records['root'])
        if manifest(api,records['root'])!=data['before']:raise SharePointProbeError('검토 이후 자료가 변경되었습니다. 종료를 다시 검토하세요.')
        remove=[data['groups'][g]['id'] for g in ('participants','executives')]
        acl(api,data,records['root'],(('admins','manage'),),allow_remove=remove)
    elif step=='year':
        parent=data['archive_parent'];bind(api,parent);admin_parent(api,data,parent['drive'],parent['item'])
        if data.get('year_record'):
            bind(api,data['year_record']);admin_parent(api,data,parent['drive'],data['year_record']['item'])
        else:data['year_record']=create_folder(api,parent['drive'],parent['item'],data['year'],guard)
    elif step=='move':
        source=bind(api,records['root']);destination=bind(api,data['year_record'])
        admin_parent(api,data,data['year_record']['drive'],data['year_record']['item'])
        now=manifest(api,records['root'])
        if content_signature(now)!=content_signature(data['before']):raise SharePointProbeError('종료 검토 후 문서 내용이 변경되었습니다.')
        if any(c['name'].casefold()==row.name.casefold() for c in api.docs.children(data['year_record']['drive'],data['year_record']['item'])):
            raise SharePointProbeError('같은 이름의 보관 폴더가 있습니다.')
        guard();data['move_job']=api.start_move(source['webUrl'],destination['webUrl'],source['eTag'])
    elif step=='poll':
        if not api.move_done(data['move_job']):return False
    elif step=='verify_move':
        if any(c['id']==data['source_root']['item'] for c in api.docs.children(drive)):
            raise SharePointProbeError('원래 프로젝트 위치에 자료가 남아 있어 종료하지 않습니다.')
        target=data['year_record'];children=api.docs.children(target['drive'],target['item'])
        matches=[c for c in children if c['name']==row.name]
        if len(matches)!=1:raise SharePointProbeError('이관 대상 폴더를 명확히 확인하지 못했습니다.')
        records['root']={'drive':target['drive'],'item':matches[0]['id'],'name':row.name,'parent':target['item']}
        tree=manifest(api,records['root'])
        if not archive_matches(data['before'],tree):raise SharePointProbeError('자료·버전 이력이 이관 전과 다릅니다.')
        for child in PROJECT_CHILDREN:
            item=tree[child];records[child]={'drive':target['drive'],'item':item['item'],'name':child,'parent':item['parent']}
        data['after']=tree
    elif step in ('sales_acl','technical_acl','archive_acl'):
        index={'archive_acl':0,'sales_acl':1,'technical_acl':2}[step]
        spec=project_folders(row.name,completed=True,year=data['year'])[index]
        record=records['root' if index==0 else PROJECT_CHILDREN[index-1]]
        acl(api,data,record,spec.grants)
    elif step=='finish_close':
        verify_groups(api,data);tree=manifest(api,records['root'],archived=True)
        if not archive_matches(data['before'],tree):raise SharePointProbeError('최종 자료 검증에 실패했습니다.')
        specs=project_folders(row.name,completed=True,year=data['year'])
        for index in (0,1,2):
            check_acl(api,data,records['root' if index==0 else PROJECT_CHILDREN[index-1]],specs[index].grants)
        # Cross-library item IDs may change. Update identities in one local transaction,
        # leaving every work-link FK intact. Unrelated references are never changed.
        data['verified_tree']=tree
        row.state='closed';row.closed_at=timezone.now()
        data.pop('move_job',None)
    else:raise SharePointProbeError('허용되지 않은 프로젝트 단계입니다.')
    return True


def advance(user,pk,revision,api=None):
    require_admin(user);key,site=scope(user);token=secrets.token_hex(16)
    with transaction.atomic():
        row=DocumentProject.objects.select_for_update().get(pk=pk,administrator=user,site_url=site,scope_key=key)
        if row.state not in ('prepared','running') or row.pending or row.revision!=revision or row.lease_until>timezone.now():
            raise SharePointProbeError('실행 중이거나 검토가 필요한 작업입니다. 다시 실행하지 않습니다.')
        try:
            with transaction.atomic():
                target=digest(['document-permission-site',site])
                DocumentPermissionLease.objects.filter(target=target,expires_at__lt=timezone.now()).delete()
                lease=DocumentPermissionLease.objects.create(target=target,expires_at=timezone.now()+timedelta(minutes=5))
        except IntegrityError:raise SharePointProbeError('다른 문서 권한 작업이 끝난 뒤 계속하세요.') from None
        step=row.data['steps'][row.cursor];row.pending=step;row.state='running'
        row.lease_token=token;row.lease_until=lease.expires_at;row.save()
    original=None
    try:
        api=api or ProjectAPI(user)
        def guard():
            user.refresh_from_db()
            if (not user.is_active or not user.is_superuser or scope(user)[0]!=key or
                not DocumentProject.objects.filter(pk=pk,lease_token=token).exists() or
                not DocumentPermissionLease.objects.filter(pk=lease.pk,expires_at__gt=timezone.now()).exists()):
                raise SharePointProbeError('관리자 연결 또는 실행 잠금이 만료되었습니다.')
        original=api.call
        def guarded(suffix,method='GET',data=None):
            if method!='GET':guard()
            return original(suffix,method,data)
        api.call=guarded;guard()
        finished=perform(api,row,step,guard)
        with transaction.atomic():
            # All external reads/writes are finished before this short commit.
            if step=='finish_close':
                for path,before in row.data['before'].items():
                    old=SharedDocument.objects.filter(site_url=site,drive_id=row.data['project_drive'],item_id=before['item']).first()
                    if not old:continue
                    new_id=row.data['verified_tree'][path]['item'];new_drive=row.data['records']['root']['drive']
                    if SharedDocument.objects.exclude(pk=old.pk).filter(site_url=site,drive_id=new_drive,item_id=new_id).exists():
                        raise SharePointProbeError('업무 문서 연결이 중복되어 별도 확인이 필요합니다.')
                    old.drive_id=new_drive;old.item_id=new_id;old.save(update_fields=['drive_id','item_id'])
                row.project.document_status='closed';row.project.save(update_fields=['document_status'])
            elif step=='finish_create':
                row.project.document_status='active';row.project.save(update_fields=['document_status'])
            if finished:
                row.cursor+=1
                row.data['results'].append({'step':LABELS.get(step,step),'at':timezone.now().isoformat()})
                write('update','DocumentProject',row.pk,'프로젝트 문서 처리',after={'단계':LABELS.get(step,step),'폴더':row.name},actor=user)
            row.revision+=1;row.pending='';row.lease_token='';row.lease_until=timezone.now();row.save()
        invalidate(site);return row,not finished
    except Exception as exc:
        import logging,traceback
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        logging.getLogger(__name__).error('project_step_failed step=%s kind=%s location=%s:%s',
            step,type(exc).__name__,frame.name,frame.lineno)
        DocumentProject.objects.filter(pk=pk,lease_token=token).update(state='attention',data=row.data,
            error=str(exc)[:190] if isinstance(exc,SharePointProbeError) else '현재 단계의 처리 결과를 확인해야 합니다.',
            lease_token='',lease_until=timezone.now())
        write('permission_attention','DocumentProject',pk,'프로젝트 처리 결과 확인 필요',after={'단계':step},actor=user)
        raise
    finally:
        if original is not None:api.call=original
        DocumentPermissionLease.objects.filter(pk=lease.pk).delete()


def reconcile_acl(user,pk,api=None):
    """Read-back only: acknowledge a completed ACL/job, never submit it again."""
    require_admin(user);key,site=scope(user);api=api or ProjectAPI(user)
    row=DocumentProject.objects.get(pk=pk,administrator=user,site_url=site,scope_key=key,state='attention',pending__in=('root_acl','poll','verify_move'))
    if row.pending=='poll':
        if not api.move_done(row.data['move_job']):raise SharePointProbeError('Microsoft에서 처리 중입니다. 잠시 후 진행 결과만 다시 조회하세요.')
    elif row.pending=='verify_move':
        def no_write():raise SharePointProbeError('재검증에서 외부 변경은 허용하지 않습니다.')
        perform(api,row,'verify_move',no_write)
    else:
        verify_groups(api,row.data)
        check_acl(api,row.data,row.data['records']['root'],project_folders(row.name)[0].grants)
    changed=DocumentProject.objects.filter(pk=pk,state='attention',pending=row.pending,revision=row.revision).update(
        state='prepared',pending='',error='',cursor=row.cursor+1,revision=row.revision+1,data=row.data)
    if changed!=1:raise SharePointProbeError('처리 기록이 변경되었습니다.')
    write('update','DocumentProject',pk,'프로젝트 처리 결과 조회 확인',after={'외부 변경':'없음','확인 단계':row.pending},actor=user)
    row.refresh_from_db();return row
