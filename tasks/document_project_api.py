"""Same-site delegated move jobs. No copy/delete emulation, redirects or retries."""
import json
import requests
from urllib.parse import urlsplit
from .group_permission_api import GroupPermissionClient, entries
from .shared_documents import GRAPH, segment, cipher
from .sharepoint_probe import SharePointProbeError, _json_request


class ProjectAPI(GroupPermissionClient):
    def graph(self, drive, item, suffix=''):
        self.docs.library(drive)
        return _json_request(f'{GRAPH}/drives/{segment(drive)}/items/{segment(item)}'+suffix, token=self.docs.token)

    def versions(self, drive, item):
        raw=self.graph(drive,item,'/versions?$top=500')
        return sorted([{'id':str(r['id']),'size':int(r['size']),
                        'modified':r.get('lastModifiedDateTime'), 'actor':r.get('lastModifiedBy')}
                       for r in entries(raw)],key=lambda r:r['id'])

    def site_call(self, action, data):
        if action not in ('CreateCopyJobs','GetCopyJobProgress'):
            raise SharePointProbeError('허용되지 않은 이관 요청입니다.')
        try:
            with requests.post(self.config.site_url+'/_api/site/'+action, json=data,
                    headers={'Authorization':'Bearer '+self.token,'Accept':'application/json;odata=nometadata',
                             'Content-Type':'application/json;odata=nometadata'},
                    timeout=(10,20),allow_redirects=False) as response:
                if response.status_code!=200 or len(response.content)>1_000_000:
                    raise SharePointProbeError('이관 응답을 확인하지 못했습니다. 중복 실행하지 마세요.')
                result=response.json()
                if not isinstance(result,dict):raise ValueError
                return result.get('d',result)
        except (requests.RequestException,ValueError,TypeError):
            raise SharePointProbeError('이관 결과 확인이 필요합니다. 자동 재실행하지 않습니다.') from None

    def start_move(self, source, destination, etag):
        for value in (source,destination):
            p=urlsplit(value);base=urlsplit(self.config.site_url)
            if (p.scheme!='https' or p.netloc!=base.netloc or p.query or p.fragment or
                    not p.path.startswith(base.path.rstrip('/')+'/')):
                raise SharePointProbeError('전용 사이트 안의 이관 위치를 확인하세요.')
        raw=self.site_call('CreateCopyJobs',{'exportObjectUris':[source],'destinationUri':destination,
            'options':{'IsMoveMode':True,'IgnoreVersionHistory':False,'MoveButKeepSource':False,
                       'AllowSchemaMismatch':False,'AllowSmallerVersionLimitOnDestination':False,
                       'NameConflictBehavior':0,'BypassSharedLock':False,'ClientEtags':[etag]}})
        jobs=raw.get('value',raw.get('CreateCopyJobs',{}).get('results',[]))
        if len(jobs)!=1 or not all(jobs[0].get(k) for k in ('JobId','JobQueueUri','EncryptionKey')):
            raise SharePointProbeError('이관 작업 접수 결과를 확인해야 합니다.')
        # Queue URI and encryption key are credentials; never expose in templates or audit.
        return cipher().encrypt(json.dumps({k:jobs[0][k] for k in
            ('JobId','JobQueueUri','EncryptionKey')}).encode()).decode()

    def move_done(self, encrypted):
        info=json.loads(cipher().decrypt(encrypted.encode()))
        raw=self.site_call('GetCopyJobProgress',{'copyJobInfo':info})
        result=raw.get('GetCopyJobProgress',raw)
        logs=result.get('Logs',[])
        if isinstance(logs,dict):logs=logs.get('results',[])
        for value in logs:
            log=json.loads(value) if isinstance(value,str) else value
            if not isinstance(log,dict):raise SharePointProbeError('이관 로그 형식을 확인해야 합니다.')
            code=log.get('ErrorCode')
            if log.get('Event') in ('JobError','JobFatalError') or code not in (None,0,'0',''):
                import re
                safe_code=re.sub(r'[^A-Za-z0-9_.-]','',str(code or log.get('Event','unknown')))[:80]
                raise SharePointProbeError('Microsoft 이관 오류 ('+safe_code+'). 현재 원본과 보관 위치를 확인해야 합니다.')
        return result.get('JobState')==0
