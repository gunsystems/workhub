// Prevent regenerated grouping previews from overwriting a manager's manual draft.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, 'static/tasks/report_grouping.js'), 'utf8');
const field = value => ({value, events: {}, addEventListener(name, callback) { this.events[name] = callback; }, dispatchEvent() {}});
const button = field('');
const status = {textContent: ''};
const panel = {hidden: false, querySelector(selector) { return selector === '[data-apply-group-draft]' ? button : (selector === '[data-generate-group-draft]' ? null : status); }};
const nodes = {id_report_type: field('weekly'), id_summary: field('직접 쓴 팀 요약'), id_next_week_plan: field('직접 쓴 계획'), id_report_issues: field('직접 쓴 이슈'),
  'report-group-draft': {textContent: JSON.stringify({current: '고객사별 초안', next: '차주 그룹', issues: '<script>문자열</script>'})}};
vm.runInNewContext(source, {document: {querySelector: () => panel, getElementById: id => nodes[id]}, Event: class {}});
assert.equal(nodes.id_summary.value, '직접 쓴 팀 요약');
assert.equal(nodes.id_next_week_plan.value, '직접 쓴 계획');
button.events.click();
assert.equal(nodes.id_summary.value, '고객사별 초안');
assert.equal(nodes.id_next_week_plan.value, '차주 그룹');
assert.equal(nodes.id_report_issues.value, '<script>문자열</script>');
assert.match(status.textContent, /임시 저장/);
nodes.id_report_type.value = 'daily'; nodes.id_report_type.events.change();
assert.equal(panel.hidden, true);
nodes.id_report_type.value = 'weekly'; nodes.id_report_type.events.change();
assert.equal(panel.hidden, false);
vm.runInNewContext(source, {document: {querySelector: () => null, getElementById: () => null}});
console.log('Report grouping: explicit apply, manual draft preservation and report type scenarios passed.');
