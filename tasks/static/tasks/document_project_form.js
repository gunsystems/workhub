(() => {
  const form = document.querySelector('.project-folder-form');
  if (!form) return;
  const value = name => form.elements.namedItem(name)?.value || '';
  const update = () => {
    const internal = value('mode') === 'internal';
    for (const name of ['customer','vendor','kind','internal']) {
      const field = form.elements.namedItem(name);
      const enabled = name === 'internal' ? internal : !internal;
      field.disabled = !enabled;
      field.closest('p').hidden = !enabled;
    }
    const parts = internal ? [value('year'),'내부',value('internal')] :
      [value('year'),value('customer'),value('vendor'),value('kind')];
    form.querySelector('[data-project-name]').textContent = parts.join('_');
  };
  form.addEventListener('input',update); form.addEventListener('change',update); update();
})();
