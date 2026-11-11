(() => {
  const decision = document.querySelector('[data-report-review-decision]');
  const body = document.querySelector('[data-report-review-body]');
  const label = document.querySelector('[data-report-review-label]');
  if (!decision || !body || !label) return;
  const update = () => {
    const required = decision.value === 'returned';
    body.required = required;
    label.textContent = required ? '사유·피드백 · 필수' : '사유·피드백 · 선택';
  };
  decision.addEventListener('change', update);
  update();
})();
