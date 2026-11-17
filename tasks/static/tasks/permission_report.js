(() => {
  'use strict';
  const root = document.querySelector('[data-permission-report]');
  if (!root) return;
  const tree = root.querySelector('[data-report-tree]'), status = root.querySelector('[data-report-status]');
  const search = root.querySelector('[data-report-search]'), stop = root.querySelector('[data-report-stop]');
  const libraries = JSON.parse(document.getElementById('permission-report-libraries').textContent);
  let controller, generation = 0;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
  function filter() {
    const q = search.value.trim().toLocaleLowerCase();
    function visit(n) {
      const own = n._search.toLocaleLowerCase().includes(q);
      const children = Array.from(n._children.children).map(visit);
      n.hidden = !!q && !own && !children.some(Boolean);
      if (q && !n.hidden) n.open = true;
      return !n.hidden;
    }
    const found = Array.from(tree.children).map(visit).some(Boolean);
    root.querySelector('[data-report-empty]').hidden = found || !q;
  }
  function makeNode(name, parent) {
    const n = el('details', '', 'report-node'); n.open = true;
    const head = el('summary'); head.append(el('strong', name), el('span', '조회 대기', 'report-badge'));
    n._body = el('div', '', 'report-body'); n._children = el('div', '', 'report-children');
    n._search = name; n.append(head, n._body, n._children); parent.append(n); return n;
  }
  async function get(url, signal) {
    const r = await fetch(url, {signal, credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
    if (!r.ok || !r.headers.get('content-type')?.includes('application/json')) throw Error('다시 조회해 주세요.');
    return r.json();
  }
  async function run() {
    if (controller) controller.abort();
    const my = ++generation; controller = new AbortController(); const signal = controller.signal;
    tree.replaceChildren(); stop.disabled = false;
    const queue = libraries.map(l => ({drive:l.id, item:'root', node:makeNode(l.name, tree), depth:0}));
    const seen = new Set(), members = new Map(); let count = 0, errors = 0;
    async function groupMembers(id) {
      if (!members.has(id)) members.set(id, get(root.dataset.memberUrl.replace('/0/', '/' + encodeURIComponent(id) + '/'), signal));
      return members.get(id);
    }
    try {
      while (queue.length) {
        if (signal.aborted) throw new DOMException('중지', 'AbortError');
        if (seen.size >= 500) { errors++; break; }
        const task = queue.shift(), key = task.drive + '/' + task.item;
        if (seen.has(key)) { errors++; continue; } seen.add(key);
        status.textContent = `조회 중 · 폴더 ${count}곳 확인 · 대기 ${queue.length + 1}곳`;
        let cursor = '', pages = 0; const cursors = new Set();
        try {
          do {
            if (++pages > 500) throw Error('항목이 많아 조회를 중단했습니다.');
            const url = new URL(root.dataset.nodeUrl, location.origin);
            url.search = new URLSearchParams({drive:task.drive,item:task.item,cursor});
            const data = await get(url, signal);
            if (my !== generation) return;
            if (pages === 1) {
              task.node.querySelector('.report-badge').textContent = data.unique ? '별도 설정' : '상위 권한 사용';
              task.node._body.append(el('p', '권한 원본: ' + data.source, 'report-source'));
              const list = el('ul', '', 'report-grants'); task.node._body.append(list);
              for (const grant of data.grants) {
                const li = el('li', '', 'report-grant');
                li.append(el('strong', `${grant.name} · ${grant.role}`));
                task.node._search += ' ' + grant.name + ' ' + grant.role;
                if (grant.kind === 'group') {
                  try {
                    const result = await groupMembers(grant.id);
                    if (my !== generation) return;
                    const text = result.members.length ? result.members.map(m =>
                      (m.name || m.email || '이름 없음') + (m.kind !== 'person' ? ' (그룹·기타 대상: 구성원 별도 확인)' : '')).join(' · ') : '구성원 없음';
                    li.append(el('span', text, 'report-members')); task.node._search += ' ' + text + ' ' + result.members.map(m => m.email).join(' ');
                  } catch (e) {
                    if (signal.aborted) throw e;
                    errors++; li.append(el('span', '구성원 확인 필요', 'report-error'));
                  }
                } else {
                  const text = grant.kind === 'person' ? '개인 직접 권한' + (grant.email ? ' · ' + grant.email : '') : '그룹·특수 대상: 구성원 별도 확인';
                  li.append(el('span', text, 'report-members')); task.node._search += ' ' + text;
                }
                list.append(li);
              }
              const link = el('a', '접근 관리', 'report-edit'); link.href = data.manage_url; task.node._body.append(link);
              task.node._body.append(el('div', '확인: ' + new Date(data.at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}), 'report-stamp'));
            }
            for (const child of data.children) {
              if (seen.size + queue.length >= 500) throw Error('폴더 수가 많아 일부 조회만 표시합니다.');
              const n = makeNode(child.name, task.node._children);
              if (task.depth >= 63) { errors++; n._body.append(el('p', '폴더 단계가 많아 확인이 필요합니다.', 'report-error')); }
              else queue.push({drive:task.drive,item:child.id,node:n,depth:task.depth+1});
            }
            cursor = data.cursor;
            if (cursor && cursors.has(cursor)) throw Error('목록이 반복됩니다.');
            if (cursor) cursors.add(cursor);
          } while (cursor);
          count++;
        } catch (e) {
          if (signal.aborted) throw e;
          errors++; task.node.querySelector('.report-badge').textContent = '일부 확인 필요';
          task.node._body.append(el('p', '권한 또는 하위 폴더 조회 실패 · 최신 권한 조회로 다시 확인하세요.', 'report-error'));
        }
        filter();
      }
      status.textContent = errors ? `일부 조회 완료 · 폴더 ${count}곳 · 확인 필요 ${errors}건. 미확인 위치를 접근 불가로 판단하지 마세요.` : `전체 조회 완료 · 문서함 ${libraries.length}개 · 하위 폴더 ${Math.max(0,count-libraries.length)}개`;
    } catch (e) { if (my === generation) status.textContent = `조회 중지 · 폴더 ${count}곳 확인. 대기 중인 폴더는 아직 확인되지 않았습니다.`; }
    finally { if (my === generation) { stop.disabled = true; filter(); } }
  }
  search.addEventListener('input', filter);
  root.querySelector('[data-report-refresh]').addEventListener('click', run);
  stop.addEventListener('click', () => controller?.abort());
  for (const [selector, open] of [['[data-report-expand]',true],['[data-report-collapse]',false]]) root.querySelector(selector).addEventListener('click', () => tree.querySelectorAll('details').forEach(n => {n.open = open;}));
  run();
})();
