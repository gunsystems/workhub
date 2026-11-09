// Standalone date-only form checks: node tasks/test_report_form.cjs
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'static/tasks/report_form.js'),'utf8');
function setup(fresh=true) {
  const field=value=>({value,events:{},addEventListener(n,f){this.events[n]=f;},fire(n){this.events[n]?.();}});
  const nodes={id_report_type:field('weekly'),id_period_start:field('2028-02-07'),id_period_end:field('2028-02-13'),'report-default-period':field('')};
  const form={dataset:{reportToday:'2028-02-12',newReport:String(fresh)}};
  vm.runInNewContext(source,{document:{querySelector:()=>form,getElementById:id=>nodes[id]}});
  return nodes;
}
let n=setup();n.id_report_type.value='daily';n.id_report_type.fire('change');assert.equal(n.id_period_start.value,'2028-02-12');assert.equal(n.id_period_end.value,'2028-02-12');
n.id_report_type.value='monthly';n.id_report_type.fire('change');assert.equal(n.id_period_start.value,'2028-02-01');assert.equal(n.id_period_end.value,'2028-02-29');
n=setup();n.id_period_start.value='2027-12-01';n.id_period_start.fire('change');n.id_report_type.value='monthly';n.id_report_type.fire('change');assert.equal(n.id_period_start.value,'2027-12-01');n['report-default-period'].fire('click');assert.equal(n.id_period_start.value,'2028-02-01');
n=setup(false);n.id_report_type.value='daily';n.id_report_type.fire('change');assert.equal(n.id_period_start.value,'2028-02-07');n.id_report_type.value='period';n.id_report_type.fire('change');assert.equal(n['report-default-period'].disabled,true);
process.stdout.write('Report date helper: 4 scenarios passed.\n');
