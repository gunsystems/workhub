const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const handlers={},calls=[],states=[],status={};
const detail={inert:false,innerHTML:'list',querySelector:()=>null,querySelectorAll:()=>[],addEventListener(n,f){handlers['detail:'+n]=f;},focus(){}};
const library={addEventListener(n,f){handlers['library:'+n]=f;},dataset:{drive:'drive'}};
const workspace={dataset:{memberUrl:'/work/shared-documents/permissions/report/groups/0/'},addEventListener(n,f){handlers[n]=f;}};
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
 await memberScenario(false);await memberScenario(true);
})().catch(e=>{console.error(e);process.exitCode=1;});

async function memberScenario(fail){
 const source=fs.readFileSync('tasks/static/tasks/group_permissions.js','utf8'),requests=[];
 class Node{constructor(){this.dataset={};this.children=[];this.isConnected=true;this.textContent='';}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}querySelector(){return null;}}
 const first=new Node(),duplicate=new Node(),empty=new Node();first.dataset.group=duplicate.dataset.group='19';empty.dataset.group='4';
 first.append(Object.assign(new Node(),{textContent:'최근 확인 구성원 · 김예린'}));
 const fakeDetail={querySelector:()=>null,querySelectorAll:()=>[first,duplicate,empty],addEventListener(){}};
 vm.runInNewContext(source,{document:{querySelector:()=>({dataset:{memberUrl:'/groups/0/'},addEventListener(){}}),getElementById:id=>id==='permission-detail'?fakeDetail:{addEventListener(){}},createElement:()=>new Node()},
  fetch:async url=>{requests.push(String(url));if(fail&&String(url).includes('/19/'))return{ok:false};return{ok:true,redirected:false,headers:{get:()=>'application/json'},json:async()=>({members:String(url).includes('/19/')?[{name:'이지수',email:'staff@example.com',kind:'person'}]:[],at:'2026-11-17T00:00:00Z'})};},
  window:{addEventListener(){}},URL,URLSearchParams});
 for(let i=0;i<15;i++)await tick();
 assert.equal(requests.filter(x=>x.includes('/19/')).length,1,'one live group lookup per panel, even with repeated previews');
 const text=first.children.map(n=>n.textContent).join(' '),none=empty.children.map(n=>n.textContent).join(' ');
 assert.match(none,/현재 그룹 구성원 · 0명/);assert.match(none,/등록된 구성원이 없습니다/);
 if(fail){assert.match(text,/최근 확인 구성원/);assert.match(text,/현재 구성원을 확인하지 못했습니다/);assert.doesNotMatch(text,/0명/);}
 else{assert.match(text,/현재 그룹 구성원 · 1명/);assert.match(text,/이지수/);assert.doesNotMatch(text,/최근 확인/);}
 console.log('Access editor member preview '+(fail?'failure preservation':'live Korean names and empty group')+' passed');
}
