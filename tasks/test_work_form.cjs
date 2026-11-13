const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fields={},panels=[];
function panel(children=[]){const p={hidden:false,querySelectorAll(){return children}};panels.push(p);return p;}
function field(name,value='',required=false){const el={value,required,disabled:false,handlers:{},addEventListener(k,f){this.handlers[k]=f}};el.box=panel([el]);el.closest=()=>el.box;fields['id_'+name]=el;return el;}
const mode=field('management_mode','report'),submit={textContent:'업무 등록'},label={textContent:'총괄 담당자:'};
for(const name of ['description','title','attachment','assignee','category','new_category','completion_criteria','customer','vendor','solution','project','status','allocation','teams','collaborators','report_days','review_days','due_date','approver_1','no_approval','quick_link'])field(name);
fields.id_title.value='작성하던 제목';fields.id_description.value='작성하던 내용';fields.id_assignee.value='8';fields.id_due_date.value='2030-02-01';fields.id_due_date.required=true;fields.id_attachment.files=['retained-reference.pdf'];fields.id_approver_1.value='9';fields.id_no_approval.checked=false;
const approvals=panel([fields.id_approver_1,fields.id_no_approval]);const dates=panel([fields.id_due_date,fields.id_report_days,fields.id_review_days]);const quick=panel([fields.id_quick_link]);
const customer=panel([fields.id_customer,fields.id_vendor,fields.id_solution]);fields.id_customer.closest=s=>s==='details'?customer:fields.id_customer.box;
const form={querySelector(s){return {'[data-work-submit]':submit,'[data-standard-section]':dates}[s]||null},querySelectorAll(s){return s==='[data-quick-only]'?[quick]:[]}};mode.closest=()=>form;
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/work_form.js','utf8'),{document:{getElementById(id){return id==='approval-fields'?approvals:fields[id]},querySelector(){return label}},Map,window:{confirm(){throw Error('Unexpected navigation prompt')},location:{assign(){throw Error('Unexpected navigation')}}}});
mode.value='quick';mode.handlers.change();assert.equal(dates.hidden,true);assert.equal(fields.id_due_date.disabled,true);assert.equal(fields.id_due_date.required,false);assert.equal(fields.id_description.required,true);assert.equal(submit.textContent,'간단 업무 전달');assert.equal(fields.id_title.value,'작성하던 제목');assert.deepEqual(fields.id_attachment.files,['retained-reference.pdf']);assert.equal(fields.id_assignee.value,'8');
mode.value='event';mode.handlers.change();assert.equal(fields.id_due_date.required,true);assert.equal(fields.id_due_date.value,'2030-02-01');assert.equal(fields.id_approver_1.disabled,true);
mode.value='report';mode.handlers.change();assert.equal(fields.id_approver_1.value,'9');assert.equal(fields.id_approver_1.disabled,false);assert.equal(fields.id_no_approval.checked,false);assert.equal(fields.id_description.value,'작성하던 내용');assert.equal(submit.textContent,'업무 등록');
console.log('Work mode switching retains common fields, files and approval choices without navigation; unrelated required fields disabled');

