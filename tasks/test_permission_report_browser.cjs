const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.handlers={};this.textContent='';this.value='';}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}
 addEventListener(n,f){this.handlers[n]=f;}setAttribute(n,v){this[n]=v;}
 querySelectorAll(s){return this.children.flatMap(n=>[...(s==='details'&&n.tagName==='details'?[n]:[]),...n.querySelectorAll(s)]);}
 querySelector(s){return this.parts?.[s];}
}
const root=new Element(),tree=new Element(),status=new Element(),search=new Element();
root.dataset={statusUrl:'/report/status/',refreshUrl:'/report/refresh/',namesUrl:'/report/names/'};
root.parts={'[data-report-tree]':tree,'[data-report-status]':status,'[data-report-search]':search};
for(const key of ['refresh','expand','collapse','empty','updated','name-links','name-rows','name-count'])root.parts[`[data-report-${key}]`]=new Element();
root.parts['[name=csrfmiddlewaretoken]']=new Element();root.parts['[name=csrfmiddlewaretoken]'].value='csrf';
const node=(key,parent,name)=>({key,parent,name,unique:true,source:'공통',at:'2026-11-17T00:00:00Z',manage_url:'/manage/',grants:[{id:19,name:'전 직원',role:'보기',kind:'group',members:[{name:'김예린',email:'staff@example.com',kind:'person'}]}]});
const initial={at:'2026-11-17T00:00:00Z',pending:false,progress:2,error:'',payload:{nodes:[node('root',null,'공통'),node('folder','root','회사 양식')]},unmatched:[],directory:[]};
const calls=[],timers=[];let response=initial;
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/permission_report.js','utf8'),{
 document:{querySelector:()=>root,createElement:t=>new Element(t),getElementById:()=>({textContent:JSON.stringify(initial)})},
 URL,URLSearchParams,location:{origin:'https://workhub.test'},setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){},
 fetch:async(url,options)=>{calls.push({url:String(url),options});if(response instanceof Error)throw response;return{ok:true,headers:{get:()=>'application/json'},json:async()=>response};}
});
(async()=>{
 assert.equal(calls.length,0,'stored complete tree renders immediately without any Microsoft or fetch request');
 assert.equal(tree.children[0]._children.children.length,1);assert.match(status.textContent,/저장된 현황/);
 search.value='김예린';search.handlers.input();assert.equal(tree.children[0].hidden,false);
 root.parts['[data-report-collapse]'].handlers.click();assert.equal(tree.children[0].open,false);
 response={...initial,pending:true,payload:null};await root.parts['[data-report-refresh]'].handlers.click();
 assert.equal(calls[0].options.method,'POST');assert.equal(calls[0].options.headers['X-CSRFToken'],'csrf');
 assert.equal(tree.children.length,1,'refresh must keep the previous complete report visible');assert.match(status.textContent,/저장된 현황을 계속/);
 assert.equal(timers.at(-1).ms,3000);
 response={...initial,at:'2026-11-17T01:00:00Z'};await timers.at(-1).fn();
 assert.match(calls.at(-1).url,/status/);assert.match(calls.at(-1).url,/known=/);
 assert.equal(tree.children[0].open,true,'active staff search expands matching ancestors');
 response=Error('offline');await root.parts['[data-report-refresh]'].handlers.click();
 assert.equal(tree.children.length,1);assert.match(status.textContent,/최신 정보 확인 필요/);
 console.log('Cached immediate rendering, CSRF queue-only refresh, retained tree, status polling, staff search and failure retention passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
