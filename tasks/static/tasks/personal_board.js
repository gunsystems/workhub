(() => {
let dragged=null;
const status=document.getElementById('move-status');
document.querySelectorAll('.personal-card[draggable="true"]').forEach(card=>{card.addEventListener('dragstart',event=>{dragged=card;document.querySelectorAll('.card-menu[open]').forEach(menu=>menu.open=false);event.dataTransfer.setData('text/plain',card.dataset.action);});card.addEventListener('dragend',()=>{dragged=null;document.querySelectorAll('.drop-hover').forEach(col=>col.classList.remove('drop-hover'));});});
document.querySelectorAll('[data-column]').forEach(col=>{
col.addEventListener('dragover',event=>{if(dragged){event.preventDefault();col.classList.add('drop-hover');}});
col.addEventListener('dragleave',()=>col.classList.remove('drop-hover'));
col.addEventListener('drop',async event=>{event.preventDefault();col.classList.remove('drop-hover');if(!dragged)return;const card=dragged;dragged=null;const anchor=event.target.closest('.personal-card');const body=new URLSearchParams({action:'move',column:col.dataset.column,before:anchor&&anchor!==card?anchor.dataset.card:'',csrfmiddlewaretoken:document.querySelector('input[name=csrfmiddlewaretoken]').value});try{const response=await fetch(card.dataset.action,{method:'POST',body,headers:{'X-Requested-With':'fetch'}});if(!response.ok)throw new Error();const result=await response.json();if(result.completed){try{sessionStorage.setItem('workhub-board-message',result.repeated?'완료했습니다. 다음 반복 카드가 준비되었습니다.':'카드를 완료했습니다.');}catch{}}location.reload();}catch{status.textContent='목록 이동에 실패했습니다. 화면을 새로 열어 다시 시도하세요.';}});
});
try{const message=sessionStorage.getItem('workhub-board-message');if(message){status.textContent=message;sessionStorage.removeItem('workhub-board-message');}}catch{}
})();
