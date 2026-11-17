const assert=require('node:assert/strict'), fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.handlers={};this.textContent='';this.value='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
  addEventListener(name,fn){this.handlers[name]=fn;}
  querySelectorAll(selector){return this.children.flatMap(n=>[...(selector==='details'&&n.tagName==='details'?[n]:[]),...n.querySelectorAll(selector)]);}
  querySelector(selector){if(selector==='.report-badge') return this.children[0].children[1];return this.parts?.[selector];}
}
const tick=()=>new Promise(r=>setImmediate(r));
const source=fs.readFileSync('tasks/static/tasks/permission_report.js','utf8');
function mount(fail=false){
  const root=new Element(),tree=new Element(),status=new Element(),search=new Element();
  root.dataset={nodeUrl:'/report/node/',memberUrl:'/report/groups/0/'};
  root.parts={'[data-report-tree]':tree,'[data-report-status]':status,'[data-report-search]':search};
  for(const key of ['stop','refresh','expand','collapse','empty'])root.parts[`[data-report-${key}]`]=new Element();
  const calls=[];let nodes=0;
  const data=(name,children,cursor='')=>({name,unique:true,source:'문서함',at:'2026-11-17T00:00:00Z',manage_url:'/manage/',
    grants:[{id:19,name:'전 직원',role:'보기',kind:'group'}],children,cursor});
  vm.runInNewContext(source,{document:{querySelector:()=>root,createElement:tag=>new Element(tag),getElementById:()=>({textContent:JSON.stringify([{id:'drive',name:'전사 공통'}])})},
    URL,URLSearchParams,location:{origin:'https://workhub.test'},AbortController,DOMException,
    fetch:async url=>{url=new URL(url,'https://workhub.test');calls.push(url.href);
      let result;if(url.pathname.includes('groups')){if(fail)return{ok:false,headers:{get:()=>''}};result={members:[{name:'김예린',email:'staff@example.com',kind:'person'}]};}
      else {nodes++;result=url.searchParams.get('item')==='root'?(url.searchParams.get('cursor')?data('전사 공통',[{id:'second',name:'두번째 폴더'}]):data('전사 공통',[{id:'first',name:'회사 양식'}],'page2')):data('하위',[]);}
      return{ok:true,headers:{get:()=>'application/json'},json:async()=>result};}
  });
  return{root,tree,status,search,calls};
}
(async()=>{
  const ui=mount();for(let i=0;i<50&&!ui.status.textContent.includes('조회 완료');i++)await tick();
  assert.match(ui.status.textContent,/전체 조회 완료.*하위 폴더 2개/);
  assert.equal(ui.calls.filter(x=>x.includes('/groups/19/')).length,1,'shared group members read once per report, not once per folder');
  assert.equal(ui.tree.children[0]._children.children.length,2,'drains bounded pages without losing later folders');
  ui.search.value='김예린';ui.search.handlers.input();assert.equal(ui.tree.children[0].hidden,false);
  ui.root.parts['[data-report-collapse]'].handlers.click();assert.equal(ui.tree.children[0].open,false);
  const bad=mount(true);for(let i=0;i<50&&!bad.status.textContent.includes('조회 완료');i++)await tick();
  assert.match(bad.status.textContent,/일부 조회 완료/);assert.doesNotMatch(bad.status.textContent,/^전체/);
  const content=bad.tree.children[0]._body.children[1].children[0].children.map(n=>n.textContent).join(' ');
  assert.match(content,/구성원 확인 필요/);assert.doesNotMatch(content,/구성원 없음/);
  console.log('Permission report pagination, group request deduplication, staff search, collapse and partial-failure checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
