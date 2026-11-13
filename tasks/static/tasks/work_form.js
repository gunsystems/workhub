(() => {
  const mode = document.getElementById('id_management_mode');
  const approvals = document.getElementById('approval-fields');
  if (!mode || !approvals) return;
  const form = mode.closest('form');
  const remembered = new Map();
  function toggle(container, hidden) {
    if (!container) return;
    container.hidden = hidden;
    for (const field of container.querySelectorAll('input,select,textarea')) {
      if (!remembered.has(field)) remembered.set(field, {disabled:field.disabled,required:field.required});
      const original = remembered.get(field);
      field.disabled = hidden || original.disabled;
      field.required = hidden ? false : original.required;
    }
  }
  function field(name, hidden) {toggle(document.getElementById(`id_${name}`)?.closest('.workspace-field'),hidden);}
  const submit = form.querySelector('[data-work-submit]');
  const submitLabel = submit?.textContent;
  const assigneeLabel = document.querySelector('label[for="id_assignee"]');
  const originalAssigneeLabel = assigneeLabel?.textContent;
  function update() {
    const quick = mode.value === 'quick', simple = mode.value === 'event';
    const guide = form.querySelector('[data-mode-guide]');
    if (guide) guide.textContent = quick ? '제목·업무 설명·담당자를 입력해 전달하세요. 담당자가 요청을 확인하고 결과를 회신하면 요청자가 확인 완료합니다.' : '관리 방식을 선택하세요. 간단 업무는 날짜 없이 전달하고 결과를 회신받습니다.';
    toggle(approvals, quick || simple);
    toggle(form.querySelector('[data-standard-section]'), quick);
    for (const name of ['category','new_category','completion_criteria','customer','vendor','solution','project','status','allocation','teams','collaborators']) field(name,quick || (name==='completion_criteria' && simple));
    for (const name of ['report_days','review_days']) field(name,quick || simple);
    for (const panel of form.querySelectorAll('[data-quick-only]')) toggle(panel,!quick);
    for (const panel of form.querySelectorAll('[data-standard-only]')) toggle(panel,quick);
    const customer = document.getElementById('id_customer');
    if (customer) toggle(customer.closest('details'),quick);
    const body = document.getElementById('id_description');
    if (body) body.required = quick;
    if (assigneeLabel) assigneeLabel.textContent = quick ? '담당자:' : originalAssigneeLabel;
    if (submit) submit.textContent = quick ? '간단 업무 전달' : submitLabel;
  }
  mode.addEventListener('change',update);
  update();
})();

