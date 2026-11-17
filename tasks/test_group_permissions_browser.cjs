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
 await memberScenario(false);await memberScenario(true);await addScenario();
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

async function addScenario(){
 const handlers={},calls=[],status={};
 class Node{
  constructor(tag='div'){this.tag=tag;this.dataset={};this.children=[];this.isConnected=true;this.classList={toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(name,value){this[name]=value;}
  querySelector(selector){return selector==='select'?this.children.find(n=>n.tag==='select'):null;}
  get value(){return this._value??(this.children.find(n=>n.tag==='option')?.value||'');}
  set value(value){this._value=value;}
  focus(){this.focused=true;}
 }
 const cards=new Node(),count={},save={},picker={value:'19',selectedIndex:0,options:[{value:'19',textContent:'전 직원',disabled:false}]};
 const form={action:{tagName:'INPUT',value:'configure_groups'},getAttribute:name=>name==='action'?'/work/shared-documents/drive/item/permissions/review/':null,
  querySelector:s=>({'[data-access-picker]':picker,'[data-access-cards]':cards,'[data-access-count]':count,'.access-save button':save}[s]||null),querySelectorAll:()=>cards.children};
 const add={closest:()=>form};
 const detail={querySelector:s=>s==='[data-access-editor]'?form:null,querySelectorAll:()=>[],addEventListener(){}};
 const workspace={dataset:{memberUrl:'/groups/0/'},addEventListener:(name,fn)=>handlers[name]=fn};
 vm.runInNewContext(fs.readFileSync('tasks/static/tasks/group_permissions.js','utf8'),{document:{querySelector:s=>s==='[data-access-workspace]'?workspace:null,getElementById:id=>({'permission-detail':detail,'permission-status':status,'permission-library':{addEventListener(){}}}[id]),createElement:tag=>new Node(tag)},
  location:{href:'https://workhub.test/work/shared-documents/drive/item/permissions/?edit=1'},URL,URLSearchParams,window:{addEventListener(){}},
  fetch:async(url,options)=>{calls.push({url:String(url),options});return{ok:true,redirected:false,headers:{get:()=>'application/json'},json:async()=>({members:[{name:'직원',kind:'person'}],at:'2026-11-17T00:00:00Z'})};}});
 handlers.click({target:{closest:s=>s==='[data-access-add]'?add:null}});
 for(let i=0;i<8;i++)await tick();
 assert.equal(cards.children.length,1,'group added despite hidden input named action');
 const row=cards.children[0],role=row.querySelector('select');
 assert.equal(role.name,'group_19');assert.equal(role.value,'read');
 assert.equal(count.textContent,'변경 1건');assert.equal(save.disabled,false);
 assert.equal(picker.options[0].disabled,true,'prevent duplicate group addition');
 assert.equal(calls.length,1);assert.equal(calls[0].url,'/groups/19/');
 assert.equal(calls[0].options.method,undefined,'adding a draft group does not submit permission changes');
 const link=row.children[0].children.find(n=>n.tag==='a');assert.match(String(link.href),/permissions\/\?tab=groups&group=19$/);
 console.log('Draft group add with named action input, safe relative URL, read default, duplicate prevention and GET-only member preview passed');
}
