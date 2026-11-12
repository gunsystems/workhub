const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const input={value:'old-page-token'};
let submit;
const form={addEventListener:(event,fn)=>{if(event==='submit')submit=fn;},querySelector:()=>input};
const document={cookie:'other=value; csrftoken='+ 'A'.repeat(32),
 querySelectorAll:selector=>selector==='form[action="/logout/"]'?[form]:[],
 getElementById:()=>null,addEventListener:()=>{}};
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/navigation.js','utf8'),{document});
submit();assert.equal(input.value,'A'.repeat(32));
// A second login changes the shared cookie while this page stays open.
document.cookie='csrftoken='+ 'B'.repeat(32);submit();assert.equal(input.value,'B'.repeat(32));
// Missing/malformed cookies never turn off server-side validation.
document.cookie='';submit();assert.equal(input.value,'B'.repeat(32));
document.cookie='csrftoken=malformed';submit();assert.equal(input.value,'B'.repeat(32));
console.log('Logout uses current shared token after account switching without bypassing server checks.');
