const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const kind={value:'',addEventListener:(event,fn)=>{assert.equal(event,'change');kind.change=fn;}},name={};
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/certificate_form.js','utf8'),{document:{getElementById:id=>id==='id_kind'?kind:name}});
assert.equal(name.disabled,true);assert.equal(name.required,false);
kind.value='other';kind.change();assert.equal(name.disabled,false);assert.equal(name.required,true);
kind.value='employment';kind.change();assert.equal(name.disabled,true);assert.equal(name.required,false);
kind.value='other';kind.change();assert.equal(name.disabled,false);assert.equal(name.required,true);
console.log('Certificate custom-name transitions passed');
