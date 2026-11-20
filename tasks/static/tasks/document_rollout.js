(() => {
  'use strict';
  const form = document.querySelector('[data-rollout-form]');
  if (!form) return;
  const status = document.querySelector('[data-rollout-status]');
  const progress = document.querySelector('[data-rollout-progress]');
  const list = document.querySelector('[data-rollout-results]');
  let running = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    running = true;
    const button = form.querySelector('button');
    button.disabled = true;
    let revision = Number(form.dataset.revision);
    const labels = {group:'그룹 생성', executive_member:'경영진 구성', role:'권한 수준 생성',
      library:'문서함 생성', bind_library:'문서함 연결 확인', folder:'폴더 생성', acl:'권한 적용 확인'};
    try {
      for (let count = 0; count < 100; count++) {
        status.textContent = '현재 단계를 적용·검증 중입니다. 중복 요청하지 마세요.';
        const body = new FormData(form);
        body.set('revision', String(revision));
        const response = await fetch(form.action, {method:'POST',body,credentials:'same-origin',
          cache:'no-store',headers:{Accept:'application/json'}});
        if (response.redirected || !response.headers.get('content-type')?.includes('application/json'))
          throw new Error('로그인 또는 응답 상태를 확인하세요. 자동 재실행하지 않습니다.');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '현재 단계의 적용 결과를 확인하세요.');
        revision = data.revision;
        form.dataset.revision = String(revision);
        progress.value = data.cursor;
        status.textContent = `${data.cursor} / ${data.total} 단계 완료`;
        const row = document.createElement('li');
        row.textContent = `${labels[data.last.action] || data.last.action} · ${data.last.target}`;
        list.append(row);
        if (list.children.length > 12) list.firstElementChild.remove();
        if (data.state === 'done') { window.location.reload(); return; }
      }
      throw new Error('적용 범위를 초과해 중단했습니다. 현재 결과를 확인하세요.');
    } catch (error) {
      status.textContent = error.message + ' 화면을 새로 열어 현재 기록을 확인하세요.';
      status.setAttribute('role','alert');
      button.textContent = '현재 기록 확인 필요';
    }
  });
})();
