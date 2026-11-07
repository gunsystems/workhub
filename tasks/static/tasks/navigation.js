(() => {
const toggle=document.getElementById('nav-toggle');
function closeNav(){document.body.classList.remove('nav-open');if(toggle){toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','메뉴 열기');}}
toggle?.addEventListener('click',()=>{const open=document.body.classList.toggle('nav-open');toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'메뉴 닫기':'메뉴 열기');});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeNav();document.querySelectorAll('.account-menu[open],.card-menu[open]').forEach(menu=>menu.open=false);}});
const nav=document.getElementById('sidebar-nav');
document.querySelectorAll('.nav-collapsible').forEach(group=>{const key='workhub-nav-'+nav.dataset.user+'-'+group.dataset.group;try{if(group.dataset.active!=='true'&&localStorage.getItem(key)==='open')group.open=true;}catch{}group.addEventListener('toggle',()=>{try{localStorage.setItem(key,group.open?'open':'closed');}catch{}});});
document.querySelectorAll('.card-menu').forEach(menu=>menu.addEventListener('toggle',()=>{if(!menu.open)return;document.querySelectorAll('.card-menu[open]').forEach(other=>{if(other!==menu)other.open=false;});const popup=menu.querySelector('.card-popover');const rect=menu.querySelector('summary').getBoundingClientRect();popup.style.left=Math.max(12,Math.min(rect.right-popup.offsetWidth,innerWidth-popup.offsetWidth-12))+'px';popup.style.top=Math.max(12,Math.min(rect.bottom+6,innerHeight-popup.offsetHeight-12))+'px';}));
document.addEventListener('scroll',event=>{if(event.target.closest?.('.card-popover'))return;document.querySelectorAll('.card-menu[open]').forEach(menu=>menu.open=false);},true);
document.addEventListener('click',event=>{document.querySelectorAll('.card-menu[open],.account-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
})();
