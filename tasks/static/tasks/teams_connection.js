(() => {
  const guide = document.querySelector('[data-teams-guide]');
  if (!guide) return;
  const text = document.getElementById('teams-linking-text');
  const copy = guide.querySelector('[data-copy-teams-code]');
  if (copy && text) copy.addEventListener('click', async () => {
    const status = guide.querySelector('[data-copy-status]');
    try {
      await navigator.clipboard.writeText(text.value);
      status.textContent = '복사했습니다. Teams의 Workhub 알림 채팅에 붙여 넣고 전송하세요.';
    } catch (_) {
      text.focus(); text.select();
      status.textContent = '문구를 선택했습니다. Ctrl+C 또는 길게 눌러 복사한 뒤 Teams에 붙여 넣으세요.';
    }
  });
  if (!text) return;
  const deadline = Date.now() + 10 * 60 * 1000;
  const status = guide.querySelector('[data-connection-status]');
  const check = async () => {
    if (Date.now() >= deadline) {
      if (status) status.textContent = '자동 확인 시간이 끝났습니다. 연결되지 않았다면 새 문구를 받아 다시 진행하세요.';
      return;
    }
    if (!document.hidden) {
      try {
        const response = await fetch(guide.dataset.statusUrl, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
        if (response.status === 401 || response.status === 403 || response.redirected) {
          if (status) status.textContent = '로그인 상태를 확인한 뒤 연결 상태를 다시 확인해 주세요.';
          return;
        }
        if (response.ok && (await response.json()).linked) {
          text.value = ''; text.hidden = true; if (copy) copy.hidden = true;
          if (status) status.textContent = '연결됐습니다. 연결 설정에서 받을 알림을 설정하세요.';
          window.location.replace(guide.dataset.profileUrl);
          return;
        }
      } catch (_) { /* Bounded read-only retry. Linking codes never leave via polling. */ }
    }
    window.setTimeout(check, 5000);
  };
  window.setTimeout(check, 5000);
})();
