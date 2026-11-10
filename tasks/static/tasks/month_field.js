(() => {
  document.querySelectorAll('.native-month-control').forEach(shell => {
    const input = shell.querySelector('input[type="month"]');
    const label = shell.querySelector('.native-month-value');
    if (!input || !label) return;
    const update = () => {
      const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(input.value);
      label.textContent = match ? `${match[1]}년 ${Number(match[2])}월` : '월 선택';
    };
    input.addEventListener('input', update);
    input.addEventListener('change', update);
    window.addEventListener('pageshow', update);
    if (input.form) input.form.addEventListener('reset', () => setTimeout(update, 0));
    update();
    shell.classList.add('is-enhanced');
  });
})();
