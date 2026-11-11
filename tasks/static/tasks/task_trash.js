document.querySelectorAll('form[data-trash-confirm]').forEach(form=>{
  form.addEventListener('submit',event=>{
    if(!window.confirm(form.dataset.trashConfirm))event.preventDefault();
  });
});
