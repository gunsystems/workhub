const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tasks/static/tasks/document_project_run.js','utf8');
async function run(responses) {
  let handler, calls = 0, reloads = 0;
  const status = {textContent:'',setAttribute(){}}, progress = {}, button = {};
  const form = {dataset:{revision:'4'},action:'/step/',reportValidity:()=>true,
    addEventListener:(event,fn)=>handler=fn,querySelector:()=>button};
  vm.runInNewContext(source,{document:{querySelector:s=>s==='[data-project-run-form]'?form:s==='[data-project-status]'?status:progress},
    window:{location:{reload(){reloads++;}}},FormData:class {set(){}},setTimeout:fn=>fn(),
    fetch:async()=>{calls++;return responses.shift();},Promise,Error,String});
  await handler({preventDefault(){}});
  await handler({preventDefault(){}});
  return {calls,reloads,status,form};
}
const response = data => ({ok:true,redirected:false,headers:{get:()=> 'application/json'},json:async()=>data});
(async()=>{
  let result = await run([response({state:'running',revision:5,cursor:3,total:9,waiting:true}),response({state:'closed',revision:6,cursor:9,total:9})]);
  assert.equal(result.calls,2);assert.equal(result.reloads,1);assert.equal(result.form.dataset.revision,'6');
  result = await run([{ok:false,redirected:false,headers:{get:()=> 'application/json'},json:async()=>({error:'확인 필요'})}]);
  assert.equal(result.calls,1);assert.equal(result.reloads,0);assert.match(result.status.textContent,/확인 필요/);
  result = await run([{redirected:true,headers:{get:()=> 'text/html'}}]);
  assert.equal(result.calls,1);assert.match(result.status.textContent,/로그인/);
  console.log('document project browser checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
