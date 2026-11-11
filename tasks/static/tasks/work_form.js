(() => {
  const mode = document.getElementById('id_management_mode');
  const approvals = document.getElementById('approval-fields');
  if (!mode || !approvals) return;
  function update() {
    if (mode.value === 'quick') {
      const link = document.querySelector('[data-quick-entry]');
      if (link && window.confirm('간단 업무 입력 화면으로 이동합니다. 현재 입력한 내용은 저장되지 않습니다. 이동할까요?')) window.location.assign(link.href);
      mode.value = 'report';
      return;
    }
    const simple = mode.value === 'event';
    approvals.hidden = simple;
    for (const name of ['report_days','review_days','completion_criteria']) {
      const field = document.getElementById(`id_${name}`);
      if (field) field.closest('.workspace-field').hidden = simple;
    }
    for (let n=1;n<=5;n++) {
      const field = document.getElementById(`id_approver_${n}`);
      if (simple && field) field.value = '';
    }
    const none = document.getElementById('id_no_approval');
    if (simple && none) none.checked = true;
  }
  mode.addEventListener('change',update);
  // A validation failure must leave the entered approval choices visible.
  if (!document.querySelector('.errorlist')) update();
})();
