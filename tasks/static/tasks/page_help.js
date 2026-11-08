(() => {
const dialog=document.getElementById('page-help-dialog');
if(!dialog)return;
document.querySelectorAll('[data-page-help-open]').forEach(button=>button.addEventListener('click',()=>{if(!dialog.open)dialog.showModal();}));
dialog.querySelectorAll('[data-page-help-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
})();
