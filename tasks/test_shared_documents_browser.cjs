const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('tasks/static/tasks/shared_documents.js', 'utf8');
function setup() {
  const handlers = {}, events = {}, calls = [], parsed = [], states = [];
  let workspace;
  const status = {textContent: ''};
  function fixture(ids = [], alert = false) {
    const rows = {children: ids.map(id => ({dataset: {docItem: id}})), append(row) {this.children.push(row);}};
    const pagination = {querySelectorAll() {return [];}, replaceWith(next) {workspace.pagination = next;}};
    return {rows, pagination, alert, setAttribute() {}, removeAttribute() {},
      querySelector(selector) {return selector === '[data-doc-results]' ? rows :
        selector === '[data-doc-pagination]' ? this.pagination : selector === '[role="alert"]' && alert ? {} : null;},
      replaceWith(next) {workspace = next;}};
  }
  workspace = fixture(['one']);
  const document = {getElementById(id) {return id === 'shared-document-status' ? status : workspace;},
    addEventListener(name, fn) {handlers[name] = fn;}};
  const location = {origin: 'https://workhub.test', pathname: '/work/shared-documents/',
    href: 'https://workhub.test/work/shared-documents/', assign(url) {calls.push({redirect: url});}};
  let fetchImpl = async url => ({ok: true, url, text: async () => '<fragment>'});
  const fetch = (...args) => {calls.push(args); return fetchImpl(...args);};
  vm.runInNewContext(script, {document, location, URL, URLSearchParams, FormData: class {},
    AbortController, fetch, DOMParser: class {parseFromString() {return {getElementById: () => parsed.shift()};}},
    history: {pushState(...args) {states.push(args);}},
    window: {fetch, AbortController, addEventListener(name, fn) {events[name] = fn;}}});
  const click = (url, more = false, extra = {}) => {
    let prevented = false;
    const link = {href: url, hasAttribute: () => more};
    handlers.click({button: 0, target: {closest: () => link}, preventDefault() {prevented = true;}, ...extra});
    return prevented;
  };
  return {click, calls, parsed, states, status, fixture, current: () => workspace,
    fetchWith(fn) {fetchImpl = fn;}};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  let app = setup();
  const next = app.fixture(['two']); app.parsed.push(next);
  assert.equal(app.click('/work/shared-documents/?folder=two'), true);
  await tick();
  assert.equal(app.current(), next, 'navigation replaces document workspace only');
  assert.equal(app.calls[0][1].headers['X-Workhub-Documents'], '1');
  assert.equal(app.calls[0][1].cache, 'no-store');
  assert.equal(app.states.length, 1);
  assert.equal(app.click('https://evil.test/work/shared-documents/'), false);
  assert.equal(app.click('/work/shared-documents/?folder=x', false, {ctrlKey: true}), false);

  app = setup(); app.parsed.push(app.fixture(['one', 'two']));
  app.click('/work/shared-documents/?cursor=next', true); await tick();
  assert.deepEqual(app.current().rows.children.map(row => row.dataset.docItem), ['one', 'two']);
  assert.equal(app.states.length, 0, 'load more preserves folder history');

  app = setup(); const denied = app.fixture([], true); app.parsed.push(denied);
  app.click('/work/shared-documents/?cursor=next', true); await tick();
  assert.equal(app.current(), denied, 'denied continuation removes old names');

  app = setup(); app.fetchWith(async () => ({ok: true, url: 'https://workhub.test/login/'}));
  app.click('/work/shared-documents/'); await tick();
  assert.equal(app.calls.at(-1).redirect, 'https://workhub.test/login/');

  app = setup(); app.fetchWith(async () => {throw new Error('offline');});
  app.click('/work/shared-documents/'); await tick();
  assert.match(app.status.textContent, /다시 시도/);
  assert.equal(app.current().rows.children.length, 1);

  // Repeated clicks while a request is running must not fan out into Graph calls.
  for (const more of [false, true]) {
    app = setup(); let finish;
    app.fetchWith(url => new Promise(resolve => {
      finish = () => resolve({ok: true, url, text: async () => ''});
    }));
    const url = '/work/shared-documents/?folder=same' + (more ? '&cursor=next' : '');
    app.parsed.push(app.fixture(['two']));
    app.click(url, more); app.click(url, more); app.click(url, more);
    assert.equal(app.calls.length, 1, 'identical pending navigation is requested once');
    assert.equal(app.calls[0][1].signal.aborted, false, 'duplicate click keeps original request alive');
    finish(); await tick();
    app.parsed.push(app.fixture(['three']));
    app.click(url, more);
    assert.equal(app.calls.length, 2, 'settled navigation rechecks access without response caching');
    finish(); await tick();
  }

  app = setup();
  app.fetchWith(async () => {throw new Error('offline');});
  app.click('/work/shared-documents/?folder=retry'); await tick();
  app.click('/work/shared-documents/?folder=retry'); await tick();
  assert.equal(app.calls.length, 2, 'failure releases duplicate-request guard for retry');

  app = setup(); let release;
  app.fetchWith(url => new Promise(resolve => {release = () => resolve({ok: true, url, text: async () => ''});}));
  app.click('/work/shared-documents/?folder=old');
  const latest = app.fixture(['latest']); app.parsed.push(latest);
  app.fetchWith(async url => ({ok: true, url, text: async () => ''}));
  app.click('/work/shared-documents/?folder=latest'); await tick(); release(); await tick();
  assert.equal(app.current(), latest, 'late navigation response cannot overwrite newer folder');
  console.log('Shared documents navigation, paging, auth, failure and race checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
