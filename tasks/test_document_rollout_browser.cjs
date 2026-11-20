const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tasks/static/tasks/document_rollout.js','utf8');
async function exercise(responses) {
  let handler, calls=0, reloads=0;
  const revisions=[];
  const button={disabled:false},status={setAttribute(){}},progress={},list={children:[],append(x){this.children.push(x);}};
  const form={dataset:{revision:'0'},action:'/same-origin-step',reportValidity:()=>true,
    querySelector:()=>button,addEventListener:(name,fn)=>{handler=fn;}};
  const document={querySelector:s=>({'[data-rollout-form]':form,'[data-rollout-status]':status,
    '[data-rollout-progress]':progress,'[data-rollout-results]':list}[s]),createElement:()=>({})};
  class FormData {set(key,value){this[key]=value;}}
  vm.runInNewContext(source,{document,FormData,window:{location:{reload(){reloads++;}}},
    fetch:async(url,options)=>{
      revisions.push(options.body.revision);assert.equal(options.method,'POST');
      assert.equal(options.credentials,'same-origin');
      const row=responses[calls++];
      if(row instanceof Error)throw row;
      return {ok:row.ok!==false,redirected:row.redirected||false,
        headers:{get:()=>row.type||'application/json'},json:async()=>row};
    }});
  const running=handler({preventDefault(){}});
  await handler({preventDefault(){}}); // A second click must not start another request chain.
  await running;
  return {calls,reloads,revisions,status,button};
}
(async()=>{
  const first={state:'running',revision:1,cursor:1,total:2,last:{action:'group',target:'sales'}};
  const done={state:'done',revision:2,cursor:2,total:2,last:{action:'folder',target:'common/양식'}};
  let r=await exercise([first,done]);
  assert.equal(r.calls,2);assert.deepEqual(r.revisions,['0','1']);assert.equal(r.reloads,1);
  for(const response of [{ok:false,error:'권한 변경 감지'},new Error('통신 실패'),{redirected:true}]){
    r=await exercise([response]);assert.equal(r.calls,1);assert.equal(r.reloads,0);assert.equal(r.button.disabled,true);
    assert.match(r.status.textContent,/확인/);
  }
  console.log('document rollout browser checks passed');
})().catch(e=>{console.error(e);process.exit(1);});
