(() => {
  const panel = document.querySelector('[data-report-grouping]');
  const kind = document.getElementById('id_report_type');
  if (!panel || !kind) return;
  const update = () => { panel.hidden = kind.value !== 'weekly'; };
  kind.addEventListener('change', update); update();
  const generate = panel.querySelector('[data-generate-group-draft]');
  const file = document.getElementById('id_attachment');
  const fileStatus = panel.querySelector('[data-group-file-status]');
  if (generate && file && fileStatus) generate.addEventListener('click', event => {
    if (file.files && file.files.length) {
      event.preventDefault();
      fileStatus.hidden = false;
      fileStatus.textContent = '선택한 첨부파일이 있습니다. 분류 초안을 만들려면 파일 선택을 먼저 해제하고, 초안 적용 후 파일을 다시 선택해 주세요.';
    } else fileStatus.hidden = true;
  });
  const button = panel.querySelector('[data-apply-group-draft]');
  const data = document.getElementById('report-group-draft');
  if (!button || !data) return;
  button.addEventListener('click', () => {
    const draft = JSON.parse(data.textContent);
    for (const [field, key] of [['summary', 'current'], ['next_week_plan', 'next'], ['report_issues', 'issues']]) {
      const input = document.getElementById('id_' + field);
      if (input) { input.value = draft[key] || ''; input.dispatchEvent(new Event('input', {bubbles: true})); }
    }
    panel.querySelector('[data-group-apply-status]').textContent = '분류 초안을 적용했습니다. 아래 내용을 보완한 뒤 임시 저장 또는 보고 제출을 진행하세요.';
    button.textContent = '초안 적용됨';
  });
})();
