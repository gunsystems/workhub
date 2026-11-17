const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const handlers={},calls=[],states=[],status={};
const detail={inert:false,innerHTML:'list',querySelector:()=>null,addEventListener(n,f){handlers['detail:'+n]=f;},focus(){}};
const library={addEventListener(n,f){handlers['library:'+n]=f;},dataset:{drive:'drive'}};
const workspace={addEventListener(n,f){handlers[n]=f;}};
let fetchImpl;
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/group_permissions.js','utf8'),{
 document:{querySelector:()=>workspace,getElementById:id=>({'permission-detail':detail,'permission-status':status,'permission-library':library}[id])},
 location:{href:'https://workhub.test/work/shared-documents/drive/root/permissions/',origin:'https://workhub.test'},URL,URLSearchParams,FormData:class{},confirm:()=>true,
 fetch(...args){calls.push(args);return fetchImpl(...args);},history:{pushState(...args){states.push(args);}},window:{addEventListener(){}}
});
const click=(href)=>handlers.click({button:0,preventDefault(){},target:{closest:s=>s==='a[data-permission-nav]'?{href}:null}});
(async()=>{
 assert.equal(calls.length,0,'actual folder list is server-rendered without automatic duplicate calls');
 let finish;fetchImpl=()=>new Promise(resolve=>{finish=resolve;});
 click('https://workhub.test/work/shared-documents/drive/team/permissions/?edit=1');
 assert.equal(detail.inert,true);assert.equal(new URL(calls[0][0]).searchParams.get('edit'),'1');
 finish({ok:true,redirected:false,text:async()=>'<h2>보안기술팀 폴더를 누가 사용할 수 있나요?</h2>'});await tick();
 assert.equal(detail.inert,false);assert.match(detail.innerHTML,/보안기술팀/);assert.equal(states.length,1);
 fetchImpl=async()=>({ok:true,redirected:true});click('https://workhub.test/work/shared-documents/drive/other/permissions/?edit=1');await tick();
 assert.equal(states.length,1);assert.equal(detail.inert,false);assert.match(status.textContent,/불러오지 못했습니다/);
 console.log('Folder list, explicit access-management navigation, loading protection and expired-session checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
