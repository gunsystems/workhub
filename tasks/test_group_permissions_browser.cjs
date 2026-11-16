const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('tasks/static/tasks/group_permissions.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  const handlers = {}, calls = [], states = [];
  class Element {
    constructor() { this.dataset = {}; this.children = []; this.disabled = false; }
    append(...children) { this.children.push(...children); }
    setAttribute(name, value) { this[name] = value; }
  }
  const children = new Element(), root = new Element();
  root.dataset.expand = 'root'; root.nextElementSibling = children;
  const tree = {dataset:{drive:'drive'},querySelector:()=>root,querySelectorAll:()=>[]};
  const detail = {inert:false,innerHTML:'',querySelector:()=>null,addEventListener(name, fn){handlers['detail:'+name]=fn;},focus(){}};
  const status = {}, library = {addEventListener(name, fn){handlers['library:'+name]=fn;}};
  const workspace = {dataset:{treeUrl:'/tree/'},addEventListener(name, fn){handlers[name]=fn;}};
  let fetchImpl = async () => ({ok:true,redirected:false,json:async()=>({rows:[{id:'team',name:'보안기술팀'}],cursor:''})});
  const location = {href:'https://workhub.test/work/shared-documents/drive/root/permissions/?tab=groups&group=12',origin:'https://workhub.test'};
  const document = {querySelector:()=>workspace,createElement:()=>new Element(),getElementById(id){return {'permission-tree':tree,'permission-detail':detail,'permission-status':status,'permission-library':library}[id];}};
  vm.runInNewContext(script,{document,location,URL,URLSearchParams,FormData:class{},confirm:()=>true,
    fetch(...args){calls.push(args);return fetchImpl(...args);},history:{pushState(...args){states.push(args);}},window:{addEventListener(){}}});
  const clickFolder = () => handlers.click({button:0,preventDefault(){},target:{closest(selector){return selector==='a[data-permission-nav]'?{href:'https://workhub.test/work/shared-documents/drive/team/permissions/',dataset:{folder:'team'}}:null;}}});
  return {children,root,detail,status,calls,states,clickFolder,fetchWith(fn){fetchImpl=fn;}};
}
(async()=>{
  let app=setup(); await tick();
  assert.equal(app.calls.length,1,'folder list loads without clicking an expand button');
  assert.equal(new URL(app.calls[0][0]).searchParams.get('folder'),'root');
  assert.equal(app.children.children[0].children[0].textContent,'📁 보안기술팀');
  let complete;
  app.fetchWith(()=>new Promise(resolve=>{complete=resolve;}));
  app.clickFolder();
  assert.equal(app.detail.inert,true,'old folder cannot be edited while the next folder loads');
  const request=new URL(app.calls[1][0]);
  assert.equal(request.searchParams.get('tab'),'folders','folder selection opens permission editing even from member management');
  assert.equal(request.searchParams.get('group'),null);
  complete({ok:true,redirected:false,text:async()=>'<section>보안기술팀 · 접근 권한</section>'}); await tick();
  assert.equal(app.detail.inert,false); assert.match(app.detail.innerHTML,/보안기술팀/);
  assert.equal(app.states.length,1);
  app.fetchWith(async()=>({ok:false})); app.clickFolder(); await tick();
  assert.equal(app.detail.inert,false,'failure restores interaction without replacing the current panel');
  assert.match(app.status.textContent,/조회하지 못했습니다/);
  app.fetchWith(async()=>({ok:true,redirected:true})); app.clickFolder(); await tick();
  assert.equal(app.states.length,1,'expired session does not insert a login page or update navigation');
  console.log('Folder permission auto-list, selected-folder navigation, loading guard and session failure checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
