(() => {
  'use strict';
  const workspace = document.querySelector('[data-permission-workspace]');
  if (!workspace) return;
  const tree = document.getElementById('permission-tree');
  const detail = document.getElementById('permission-detail');
  const status = document.getElementById('permission-status');
  let generation = 0;
  const dirty = () => !!detail.querySelector('.permission-changed');
  const hrefFor = (folder) => `/work/shared-documents/${encodeURIComponent(tree.dataset.drive)}/${encodeURIComponent(folder)}/permissions/`;
  async function panel(url, push = true) {
    if (dirty() && !confirm('변경한 권한이 아직 저장되지 않았습니다. 이동할까요?')) return;
    const mine = ++generation;
    const request = new URL(url);
    request.searchParams.set('panel', '1');
    status.textContent = '권한을 조회하고 있습니다.';
    detail.inert = true;
    try {
      const response = await fetch(request, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok || response.redirected) throw new Error('session');
      const html = await response.text();
      if (mine !== generation) return;
      // Only server-escaped, same-origin template fragments are inserted.
      detail.innerHTML = html;
      status.textContent = '';
      if (push) history.pushState({}, '', url);
      tree.querySelectorAll('[data-folder]').forEach(a => a.classList.toggle('active', a.href.split('?')[0] === url.href.split('?')[0]));
      detail.focus({preventScroll: true});
    } catch (_) { if (mine === generation) status.textContent = '조회하지 못했습니다. 페이지를 새로고침하여 로그인 상태를 확인하세요.'; }
    finally { if (mine === generation) detail.inert = false; }
  }
  async function expand(button) {
    if (button.disabled) return;
    let container = button.nextElementSibling;
    if (button.dataset.loaded && !button.dataset.cursor) {
      container.hidden = !container.hidden;
      button.setAttribute('aria-expanded', String(!container.hidden));
      button.textContent = button.dataset.label ? (container.hidden ? '▸' : '▾') : (container.hidden ? '폴더 목록 펼치기' : '폴더 목록 접기');
      return;
    }
    button.disabled = true;
    const url = new URL(workspace.dataset.treeUrl, location.origin);
    url.searchParams.set('drive', tree.dataset.drive);
    url.searchParams.set('folder', button.dataset.expand);
    if (button.dataset.cursor) url.searchParams.set('cursor', button.dataset.cursor);
    try {
      const response = await fetch(url, {credentials: 'same-origin'});
      if (!response.ok || response.redirected) throw new Error();
      const data = await response.json();
      if (!Array.isArray(data.rows)) throw new Error();
      for (const row of data.rows) {
        const node = document.createElement('div'); node.className = 'permission-tree-node';
        const link = document.createElement('a'); link.textContent = '📁 ' + row.name;
        link.href = hrefFor(row.id); link.dataset.permissionNav = ''; link.dataset.folder = row.id;
        const toggle = document.createElement('button'); toggle.type = 'button';
        toggle.dataset.expand = row.id; toggle.dataset.label = row.name; toggle.textContent = '▸';
        toggle.setAttribute('aria-label', row.name + ' 하위 폴더 펼치기·접기'); toggle.setAttribute('aria-expanded', 'false');
        const children = document.createElement('div'); children.dataset.children = row.id;
        node.append(link, toggle, children); container.append(node);
      }
      if (!data.rows.length && !data.cursor && !container.childElementCount) {
        const empty = document.createElement('p'); empty.className = 'helptext'; empty.textContent = '하위 폴더 없음'; container.append(empty);
      }
      button.dataset.loaded = '1'; button.dataset.cursor = data.cursor || '';
      button.textContent = data.cursor ? '폴더 더 보기' : button.dataset.label ? '▾' : '폴더 목록 접기';
      container.hidden = false; button.setAttribute('aria-expanded', 'true'); status.textContent = '';
    } catch (_) { status.textContent = '하위 폴더를 불러오지 못했습니다. 다시 시도해 주세요.'; }
    finally { button.disabled = false; }
  }
  workspace.addEventListener('click', e => {
    const all = e.target.closest('[data-select-visible]');
    if (all) {
      all.closest('form').querySelectorAll('[data-search-row]:not([hidden]) input[name=add]').forEach(input => {
        input.checked = true; input.closest('label').classList.add('permission-changed');
      });
      return;
    }
    const expandButton = e.target.closest('[data-expand]');
    if (expandButton) { expand(expandButton); return; }
    const link = e.target.closest('a[data-permission-nav]');
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    const url = new URL(link.href);
    if (link.dataset.folder) {
      url.searchParams.set('tab', 'folders');
      url.searchParams.delete('group'); url.searchParams.delete('employee');
    }
    panel(url);
  });
  detail.addEventListener('input', e => {
    if (e.target.matches('[data-row-search],[data-department-filter]')) {
      const scope = e.target.closest('form') || detail;
      const query = (scope.querySelector('[data-row-search]')?.value || '').trim().toLocaleLowerCase();
      const department = scope.querySelector('[data-department-filter]')?.value || '';
      scope.querySelectorAll('[data-search-row]').forEach(row => {
        row.hidden = !row.dataset.searchRow.toLocaleLowerCase().includes(query) || (!!department && row.dataset.department !== department);
      });
    }
    if (e.target.matches('input[name=add],input[name=remove]')) e.target.closest('label').classList.toggle('permission-changed', e.target.checked);
    if (e.target.matches('input[type=radio][data-original]')) {
      const form = e.target.closest('form');
      form.querySelectorAll('tr[data-search-row]').forEach(row => {
        const checked = row.querySelector('input:checked');
        row.classList.toggle('permission-changed', !!checked && checked.value !== checked.dataset.original);
      });
      const label = form.querySelector('[data-change-count]');
      if (label) label.textContent = `변경 ${form.querySelectorAll('.permission-changed').length}건`;
    }
  });
  detail.addEventListener('submit', e => {
    const form = e.target.closest('[data-panel-filter]');
    if (!form) return;
    e.preventDefault();
    const url = new URL(location.href); url.search = new URLSearchParams(new FormData(form)).toString(); panel(url);
  });
  document.getElementById('permission-library').addEventListener('change', e => {
    if (dirty() && !confirm('변경한 권한이 아직 저장되지 않았습니다. 이동할까요?')) { e.target.value = tree.dataset.drive; return; }
    location.href = `/work/shared-documents/${encodeURIComponent(e.target.value)}/root/permissions/`;
  });
  window.addEventListener('popstate', () => { panel(new URL(location.href), false); });
  const initialFolderList = tree.querySelector('[data-expand="root"]');
  if (initialFolderList && tree.dataset.drive) expand(initialFolderList);
})();
