const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'static/tasks/report_form.js'),'utf8');
function setup(fresh=true){
 const field=value=>({value,checked:false,hidden:false,events:{},addEventListener(n,f){this.events[n]=f;},fire(n,event={}){this.events[n]?.(event);}});
 const nodes={id_report_type:field('weekly'),id_period_start:field('2026-11-09'),id_period_end:field('2026-11-15'),id_summary:field('캘린더 초안'),id_next_week_plan:field('차주 초안'),id_report_issues:field(''), 'calendar-applied':field('0')};
 const checks=[field('1'),field('2')];checks[0].checked=true;
 const extras=Object.fromEntries(['data-period-calendar','data-weekly-guide','data-report-heading','data-report-intro','data-calendar-apply','data-calendar-preview','data-calendar-file-warning'].map(k=>[k,field('')]));
 extras['data-calendar-preview'].value='[교육] 선택한 기간 업무';
 const file={files:[]},events={};
 const form={dataset:{reportToday:'2028-02-12',newReport:String(fresh)},querySelectorAll:()=>checks,querySelector:()=>file,addEventListener(n,f){events[n]=f;}};
 vm.runInNewContext(source,{document:{querySelector:s=>s==='[data-report-today]'?form:extras[s.slice(1,-1)],querySelectorAll:()=>[{value:'2'}],getElementById:id=>nodes[id]}});
 return {nodes,checks,extras,file,events,change(value){nodes.id_report_type.value=value;nodes.id_report_type.fire('change');}};
}
let x=setup();x.change('individual');assert.equal(x.nodes.id_summary.value,'');assert.equal(x.nodes.id_next_week_plan.value,'');assert.equal(x.checks[0].checked,false);assert.equal(x.nodes.id_period_start.value,'2028-02-12');assert.equal(x.extras['data-weekly-guide'].hidden,true);
x.nodes.id_summary.value='직접 쓴 건별 결과';x.nodes.id_period_start.value='2028-02-10';x.checks[1].checked=true;x.change('period');assert.equal(x.nodes.id_summary.value,'');assert.equal(x.nodes.id_period_start.value,'');assert.equal(x.extras['data-period-calendar'].hidden,false);
x.change('individual');assert.equal(x.nodes.id_summary.value,'직접 쓴 건별 결과');assert.equal(x.nodes.id_period_start.value,'2028-02-10');assert.equal(x.checks[1].checked,true);
x.change('weekly');assert.equal(x.nodes.id_summary.value,'캘린더 초안');assert.equal(x.nodes.id_next_week_plan.value,'차주 초안');assert.equal(x.checks[0].checked,true);assert.equal(x.nodes.id_period_start.value,'2026-11-09');
x.change('period');x.nodes.id_period_start.value='2028-02-01';x.nodes.id_period_end.value='2028-02-11';assert.equal(x.nodes.id_summary.value,'');x.extras['data-calendar-apply'].fire('click');assert.equal(x.nodes.id_summary.value,'[교육] 선택한 기간 업무');assert.equal(x.checks[1].checked,true);assert.equal(x.nodes['calendar-applied'].value,'1');
x.change('individual');x.extras['data-calendar-apply'].fire('click');assert.equal(x.nodes.id_summary.value,'직접 쓴 건별 결과');x.change('period');assert.equal(x.nodes.id_summary.value,'[교육] 선택한 기간 업무');assert.equal(x.nodes.id_period_start.value,'2028-02-01');
x=setup(false);x.change('individual');assert.equal(x.nodes.id_summary.value,'캘린더 초안');assert.equal(x.nodes.id_period_start.value,'2026-11-09');
x=setup();x.change('monthly');assert.equal(x.nodes.id_period_end.value,'2028-02-29');x.change('daily');assert.equal(x.nodes.id_period_start.value,'2028-02-12');
x.file.files=[{}];let prevented=false;x.events.submit({submitter:{value:'calendar'},preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(x.extras['data-calendar-file-warning'].hidden,false);prevented=false;x.events.submit({submitter:{value:'draft'},preventDefault(){prevented=true;}});assert.equal(prevented,false);
console.log('Report kind drafts, explicit calendar apply, date defaults, saved edits and attachment preservation passed.');
