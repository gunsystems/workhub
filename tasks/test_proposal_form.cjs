const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let add;const amount={value:'300',addEventListener(){}},grand={},counter={value:'2'};
function row(name,quantity,price){const fields={name:{value:name},quantity:{value:quantity},price:{value:price},DELETE:{checked:false}},output={};return {fields,output,classList:{toggle(){}},querySelector(s){return s==='output'?output:fields[s.match(/-(\w+)/)[1]];}};}
const a=row('상품 A','2','100'),b=row('상품 B','1.5','11');let calculate;
const box={children:[a,b],addEventListener(_,fn){calculate=fn;}};
let updateLabels;const category={value:'purchase',addEventListener(_,fn){updateLabels=fn;}},dateLabel={},supplierLabel={};const stage={value:'prior',addEventListener(){}},original={closest(){return {hidden:false}}};
const form={querySelector(s){return {'[name=category]':category,'label[for=id_needed_on]':dateLabel,'label[for=id_supplier]':supplierLabel,'[name=stage]':stage,'[name=original]':original,'[data-lines]':box,'[name="lines-TOTAL_FORMS"]':counter,'[name=amount]':amount,'[data-grand-total]':grand,'[data-add-line]':{addEventListener(_,fn){add=fn;}}}[s];}};
vm.runInNewContext(fs.readFileSync('tasks/static/tasks/proposal_form.js','utf8'),{document:{querySelector:()=>form}});
assert.equal(amount.value,'217');assert.equal(grand.textContent,'217원');assert.equal(amount.readOnly,true);
b.fields.DELETE.checked=true;calculate();assert.equal(amount.value,'200');
a.fields.quantity.value='0.01';a.fields.price.value='50';calculate();assert.equal(amount.value,'1');
a.fields.DELETE.checked=true;calculate();assert.equal(amount.readOnly,false);amount.value='450';calculate();assert.equal(grand.textContent,'450원');
console.log('Proposal lines: per-line KRW rounding, total, deletion, manual total verified.');

assert.equal(dateLabel.textContent,'구매 희망일:');assert.equal(supplierLabel.textContent,'구매처:');category.value='education';updateLabels();assert.equal(dateLabel.textContent,'진행 희망일:');assert.equal(supplierLabel.textContent,'교육기관·업체:');stage.value='claim';updateLabels();assert.equal(dateLabel.textContent,'지급 희망일:');
