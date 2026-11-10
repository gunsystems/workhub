const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fields={};
for(const name of ['request_kind','department','customer','solution','requested_start_date','response_due_date','coordinator','preferred_employee']){
 const label={badge:null,querySelector(){return this.badge},append(space,b){this.badge=b}};
 fields['id_'+name]={value:'',required:false,handlers:{},addEventListener(k,f){this.handlers[k]=f},closest(){return{querySelector(){return label}}},label};
}
fields.id_request_kind.value='cooperation';fields.id_department.value='2';
for(const n of ['coordinator','preferred_employee']){
 const f=fields['id_'+n];f.options=[{value:'',dataset:{}},{value:'1',dataset:{team:'1'}},{value:'2',dataset:{team:'2'}}];
 Object.defineProperty(f,'selectedOptions',{get(){return f.options.filter(o=>o.value===f.value)}});
}
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/request_form.js','utf8'),{document:{getElementById(id){return fields[id]},createElement(){return{dataset:{}}}}});
assert.equal(fields.id_solution.required,false);
fields.id_request_kind.value='support';fields.id_request_kind.handlers.change();assert.equal(fields.id_solution.required,true);assert.equal(fields.id_customer.label.badge.hidden,false);
assert.equal(fields.id_coordinator.options[1].disabled,true);assert.equal(fields.id_coordinator.options[2].disabled,false);
fields.id_coordinator.value='2';fields.id_department.value='1';fields.id_department.handlers.change();assert.equal(fields.id_coordinator.value,'');assert.equal(fields.id_coordinator.options[1].disabled,false);
fields.id_request_kind.value='cooperation';fields.id_request_kind.handlers.change();assert.equal(fields.id_solution.required,false);assert.equal(fields.id_customer.label.badge.hidden,true);
console.log('Request form: conditional required fields and team filtering passed');
