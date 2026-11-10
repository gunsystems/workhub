const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('tasks/static/tasks/file_upload.js','utf8');
const {validate}=require('./static/tasks/file_upload.js');
const limit=20*1024*1024;
assert.equal(validate([{name:'설계.PDF',size:limit}],limit,'.pdf,.txt'),'');
assert.match(validate([{name:'x.pdf',size:limit+1}],limit,'.pdf'),/20MB/);
assert.match(validate([{name:'x.exe',size:1}],limit,'.pdf'),/형식/);
assert.match(validate([{name:'x.pdf',size:0}],limit,'.pdf'),/내용/);
assert.match(validate([{name:'a.pdf',size:1},{name:'b.pdf',size:1}],limit,'.pdf'),/하나/);
assert.match(validate([{name:'a.pdf',size:11*1024*1024}],10*1024*1024,'.pdf'),/10MB/);
class Element {
 constructor(){this.listeners={};this.children=[];this.attrs={};this.dataset={};this.hidden=false;this.classes=new Set();this.classList={add:(...v)=>v.forEach(x=>this.classes.add(x)),remove:x=>this.classes.delete(x),toggle:(x,b)=>b?this.classes.add(x):this.classes.delete(x)};}
 addEventListener(n,f){(this.listeners[n]||=[]).push(f);}
 emit(n,event={}){event.preventDefault=()=>event.defaultPrevented=true;(this.listeners[n]||[]).forEach(f=>f(event));return event;}
 setAttribute(n,v){this.attrs[n]=v;}getAttribute(n){return this.attrs[n]||null;}
 append(...v){this.children.push(...v);}insertAdjacentElement(_,e){this.zone=e;}
 focus(){this.focused=true;}click(){this.clicked=true;}
}
const form=new Element(),input=new Element();input.form=form;input.id='file';input.dataset.uploadLimit=String(limit);input.accept='.pdf,.txt';input.files=[];input.setCustomValidity=x=>input.validity=x;
Object.defineProperty(input,'value',{set:v=>{if(v==='')input.files=[];}});
const document=new Element();document.querySelectorAll=()=>[input];document.createElement=()=>new Element();
const window=new Element();
class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)};}}
vm.runInNewContext(source,{document,window,DataTransfer:Transfer,setTimeout:f=>f(),queueMicrotask:f=>Promise.resolve().then(f),module:{exports:{}}});
const zone=input.zone,[title,hint,choose,clear,status]=zone.children;
assert.match(status.textContent,/아직 파일을 선택하지/);
const transfer={types:['Files'],files:[{name:'drop.pdf',size:100}],items:[]};
zone.emit('drop',{dataTransfer:transfer});assert.equal(input.files[0].name,'drop.pdf');assert.match(status.textContent,/아직 업로드되지/);assert.equal(clear.hidden,false);
assert.equal(form.dataset.uploadBusy,undefined); // A drop must never submit the form.
form.emit('submit');assert.match(status.textContent,/업로드 중/);assert.equal(form.emit('submit').defaultPrevented,true);
window.emit('pageshow');assert.equal(form.dataset.uploadBusy,undefined);
clear.emit('click');assert.equal(input.files.length,0);assert.equal(input.validity,'');
zone.emit('drop',{dataTransfer:{types:['Files'],files:[{name:'bad.exe',size:1}],items:[]}});assert.match(input.validity,/형식/);assert.equal(clear.hidden,false);
clear.emit('click');assert.equal(input.validity,'');
zone.emit('drop',{dataTransfer:{types:['Files'],files:[],items:[{webkitGetAsEntry:()=>({isDirectory:true})}]}});assert.match(status.textContent,/폴더/);
const outside=document.emit('drop',{dataTransfer:transfer});assert.equal(outside.defaultPrevented,true);
const text=document.emit('drop',{dataTransfer:{types:['text/plain']}});assert.equal(Boolean(text.defaultPrevented),false);
choose.emit('click');assert.equal(input.clicked,true);
console.log('File upload validation, drop selection, status, reset and navigation-guard checks passed.');
