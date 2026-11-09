/* No keystroke values or page content are collected. */
(() => {
  const root = document.querySelector('[data-idle-session]');
  if (!root) return;
  let expires = 0, dirty = false, busy = false;
  const csrf = () => document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
  const expired = () => location.assign('/login/?expired=1');
  async function check(active = false) {
    if (busy) return;
    const token = csrf();
    if (active && !token) return;
    busy = true;
    try {
      const response = await fetch('/accounts/session/' + (active ? 'activity/' : 'status/'), {
        method: active ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
        headers: active ? {'X-CSRFToken':token} : {}, redirect: 'error'
      });
      if (response.status === 401) { expired(); return; }
      if (!response.ok) { if (response.status === 403) { dirty = false; expires = 0; } return; }
      const data = await response.json();
      expires = data.expires_at * 1000;
      if (active) dirty = false;
    } catch (_) { /* Retry on the next timer; server still enforces expiry. */ }
    finally { busy = false; }
  }
  ['pointermove','pointerdown','keydown','scroll','touchstart','input'].forEach(name =>
    window.addEventListener(name, () => { dirty = true; }, {passive:true}));
  // A status read does not extend the session. Another tab's activity is visible here.
  setInterval(() => {
    if (dirty) check(true);
    else if (!expires || Date.now() >= expires) check();
  }, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  check();
})();
