(() => {
  const form = document.querySelector('[data-project-run-form]');
  if (!form) return;
  let running = false;
  form.addEventListener('submit',async event => {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    running = true;
    const button = form.querySelector('button'), status = document.querySelector('[data-project-status]');
    button.disabled = true;
    try {
      for (let attempt = 0; attempt < 80; attempt++) {
        status.textContent = '프로젝트 처리·검증 중입니다. 이 화면을 유지하세요.';
        const body = new FormData(form);
        body.set('revision', form.dataset.revision);
        const response = await fetch(form.action,{method:'POST',body,credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
        if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('로그인 또는 응답 상태를 확인하세요.');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '현재 작업의 결과 확인이 필요합니다.');
        form.dataset.revision = String(data.revision);
        document.querySelector('[data-project-progress]').value = data.cursor;
        status.textContent = `${data.cursor} / ${data.total} 단계 완료`;
        if (['active','closed'].includes(data.state)) {window.location.reload();return;}
        if (data.waiting) {
          status.textContent = 'Microsoft에서 자료를 이관하고 있습니다. 완료 상태를 확인 중입니다.';
          await new Promise(resolve => setTimeout(resolve,5000));
        }
      }
      throw new Error('처리 기록을 다시 열어 이관 진행 상태를 확인하세요.');
    } catch (error) {
      status.textContent = error.message + ' 중복 실행하지 말고 현재 기록부터 확인하세요.';
      status.setAttribute('role','alert');button.textContent = '현재 기록 확인 필요';
    }
  });
})();
