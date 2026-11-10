/* Progressive enhancement: choosing or dropping a file never uploads it. */
(() => {
  function validate(files, limit, accept) {
    if (files.length !== 1) return '파일은 한 번에 하나씩 등록해 주세요.';
    const f = files[0];
    if (!f.size) return '내용이 있는 파일을 선택해 주세요.';
    if (f.size > limit) return `파일당 ${limit / 1024 / 1024}MB 이하로 등록해 주세요.`;
    const extensions = accept.split(',').map(x => x.trim().toLowerCase()).filter(Boolean);
    if (extensions.length && !extensions.some(ext => f.name.toLowerCase().endsWith(ext)))
      return '지원하는 형식의 파일을 선택해 주세요.';
    return '';
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {validate};
  if (typeof document === 'undefined') return;
  const isFiles = e => Array.from(e.dataTransfer?.types || []).includes('Files');
  document.querySelectorAll('input[type="file"][data-upload-limit]').forEach(input => {
    if (input.dataset.dropReady) return;
    input.dataset.dropReady = '1';
    const limit = Number(input.dataset.uploadLimit), form = input.form;
    const zone = document.createElement('div'); zone.className = 'file-dropzone';
    const title = document.createElement('strong'); title.textContent = '파일을 여기로 끌어다 놓으세요';
    const hint = document.createElement('p'); hint.textContent = `또는 파일 선택 · 한 번에 1개 · 최대 ${limit / 1024 / 1024}MB`;
    const choose = document.createElement('button'); choose.type = 'button'; choose.className = 'button file-choose'; choose.textContent = '파일 선택';
    const status = document.createElement('p'); status.className = 'file-upload-status'; status.setAttribute('role','status'); status.setAttribute('aria-live','polite'); status.id = input.id + '-upload-status';
    const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'button file-clear'; clear.textContent = '선택 취소'; clear.hidden = true;
    input.insertAdjacentElement('beforebegin', zone); zone.append(title,hint,choose,clear,status,input);
    input.classList.add('file-native-input');
    input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'),status.id].filter(Boolean).join(' '));
    let depth = 0;
    function show(error = '') {
      input.setCustomValidity(error);input.setAttribute('aria-invalid',error ? 'true' : 'false');
      zone.classList.toggle('has-error', Boolean(error)); zone.classList.toggle('has-file', Boolean(input.files.length));
      clear.hidden = !input.files.length && !error;
      status.textContent = error || (input.files.length ?
        `선택됨 · ${input.files[0].name} (${(input.files[0].size / 1024).toFixed(1)}KB) · 아직 업로드되지 않았습니다. 아래 등록·저장 버튼을 눌러 완료하세요.` :
        '아직 파일을 선택하지 않았습니다. 선택 후 등록·저장 버튼을 눌러야 업로드됩니다.');
    }
    function check() {
      const error = input.files.length ? validate(Array.from(input.files),limit,input.accept) : '';
      if (error) input.value = '';
      show(error);
    }
    choose.addEventListener('click', () => input.click());
    clear.addEventListener('click', () => { input.value = ''; show(); choose.focus(); });
    input.addEventListener('change', check);
    zone.addEventListener('dragenter', e => { if (isFiles(e)) {e.preventDefault();depth++;zone.classList.add('is-dragging');} });
    zone.addEventListener('dragover', e => { if (isFiles(e)) {e.preventDefault();e.dataTransfer.dropEffect='copy';} });
    zone.addEventListener('dragleave', () => {if (--depth <= 0) {depth=0;zone.classList.remove('is-dragging');} });
    zone.addEventListener('drop', e => {
      if (!isFiles(e)) return;
      e.preventDefault();depth=0;zone.classList.remove('is-dragging');
      const directory = Array.from(e.dataTransfer.items || []).some(item => item.webkitGetAsEntry?.()?.isDirectory);
      const files = Array.from(e.dataTransfer.files);
      const error = directory ? '폴더 대신 파일 하나를 선택해 주세요.' : validate(files,limit,input.accept);
      if (error) {input.value='';show(error);return;}
      try {const transfer=new DataTransfer();transfer.items.add(files[0]);input.files=transfer.files;check();}
      catch (_) {input.value='';show('이 브라우저에서는 파일 선택 버튼으로 첨부해 주세요.');}
    });
    form?.addEventListener('reset', () => setTimeout(() => {depth=0;zone.classList.remove('is-dragging');show();},0));
    form?.addEventListener('submit', e => {
      if (e.defaultPrevented) return;
      if (form.dataset.uploadBusy) {e.preventDefault();return;}
      if (input.files.length) {form.dataset.uploadBusy='1';status.textContent='업로드 중… 저장이 끝날 때까지 이 화면을 유지해 주세요.';zone.classList.add('is-uploading');}
      queueMicrotask(() => {if(e.defaultPrevented){delete form.dataset.uploadBusy;zone.classList.remove('is-uploading');show();}});
    });
    window.addEventListener('pageshow', () => {if(form) delete form.dataset.uploadBusy;zone.classList.remove('is-uploading');show();});
    show();
  });
  // Dropping outside the input must not replace an unsaved form with a file preview.
  ['dragover','drop'].forEach(type => document.addEventListener(type,e => {if(isFiles(e)) e.preventDefault();}));
})();
