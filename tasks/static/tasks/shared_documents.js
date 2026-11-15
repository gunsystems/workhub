(() => {
  'use strict';
  const path = '/work/shared-documents/';
  const status = document.getElementById('shared-document-status');
  if (!status || !window.fetch || !window.AbortController) return;
  let pending;
  const target = value => {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || url.pathname !== path) throw new Error('destination');
    return url;
  };
  async function navigate(value, append = false, push = true) {
    let url;
    try { url = target(value); } catch (_) { return; }
    if (pending) pending.abort();
    const controller = new AbortController();
    pending = controller;
    const current = document.getElementById('shared-document-workspace');
    current.setAttribute('aria-busy', 'true');
    status.textContent = append ? '다음 항목을 불러오는 중입니다…' : '문서 목록을 불러오는 중입니다…';
    try {
      const response = await fetch(url.href, {credentials: 'same-origin', cache: 'no-store',
        headers: {'X-Workhub-Documents': '1'}, signal: controller.signal});
      if (controller.signal.aborted) return;
      const destination = new URL(response.url);
      if (destination.origin !== location.origin) throw new Error('destination');
      if (destination.pathname !== path) { location.assign(destination.href); return; }
      if (!response.ok) throw new Error('response');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (controller.signal.aborted) return;
      const next = parsed.getElementById('shared-document-workspace');
      if (!next) throw new Error('response');
      const incoming = next.querySelector('[data-doc-results]');
      const existing = current.querySelector('[data-doc-results]');
      const pagination = next.querySelector('[data-doc-pagination]');
      // Errors replace the whole workspace, including formerly visible names.
      if (append && incoming && existing && pagination && !next.querySelector('[role="alert"]')) {
        const ids = new Set([...existing.children].map(node => node.dataset.docItem));
        for (const row of [...incoming.children]) {
          if (!ids.has(row.dataset.docItem)) { existing.append(row); ids.add(row.dataset.docItem); }
        }
        if (existing.children.length) pagination.querySelectorAll('.empty').forEach(node => node.remove());
        current.querySelector('[data-doc-pagination]').replaceWith(pagination);
      } else {
        current.replaceWith(next);
      }
      if (push && !append) history.pushState({documents: true}, '', url.href);
      status.textContent = next.querySelector('[role="alert"]') ? '문서 목록을 확인하지 못했습니다.' : '문서 목록을 불러왔습니다.';
    } catch (error) {
      if (error.name === 'AbortError' || controller.signal.aborted) return;
      status.textContent = '목록을 불러오지 못했습니다. 다시 시도하거나 페이지를 새로고침해 주세요.';
    } finally {
      if (pending === controller) {
        document.getElementById('shared-document-workspace')?.removeAttribute('aria-busy');
        pending = null;
      }
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-doc-nav],a[data-doc-more]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    try { target(link.href); } catch (_) { return; }
    event.preventDefault();
    navigate(link.href, link.hasAttribute('data-doc-more'));
  });
  document.addEventListener('submit', event => {
    const form = event.target.closest('form[data-doc-search]');
    if (!form) return;
    event.preventDefault();
    const url = new URL(path, location.origin);
    url.search = new URLSearchParams(new FormData(form)).toString();
    navigate(url.href);
  });
  window.addEventListener('popstate', () => {
    if (location.pathname === path) navigate(location.href, false, false);
  });
})();
