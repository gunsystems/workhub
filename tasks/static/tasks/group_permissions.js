(() => {
  'use strict';
  const workspace=document.querySelector('[data-access-workspace]'); if(!workspace)return;
  const detail=document.getElementById('permission-detail'),status=document.getElementById('permission-status'),library=document.getElementById('permission-library');
  let generation=0;
  const dirty=()=>!!detail.querySelector('.permission-changed');
  function refresh(){
    const form=detail.querySelector('[data-access-editor]');if(!form)return;
    let count=0;
    form.querySelectorAll('[data-access-row]').forEach(row=>{const changed=row.querySelector('select').value!==row.dataset.original;row.classList.toggle('permission-changed',changed);if(changed)count++;});
    form.querySelector('[data-access-count]').textContent=`변경 ${count}건`;
    form.querySelector('.access-save button').disabled=count===0;
  }
  async function panel(url,push=true){
    if(dirty()&&!confirm('저장하지 않은 변경이 있습니다. 이동할까요?'))return;
    const mine=++generation,request=new URL(url);request.searchParams.set('panel','1');detail.inert=true;status.textContent='불러오는 중입니다.';
    try{
      const response=await fetch(request,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      if(!response.ok||response.redirected)throw new Error();
      const html=await response.text();if(mine!==generation)return;
      detail.innerHTML=html;if(push)history.pushState({},'',url);refresh();status.textContent='';detail.focus({preventScroll:true});
    }catch(_){if(mine===generation)status.textContent='불러오지 못했습니다. 로그인 상태를 확인하고 다시 시도하세요.';}
    finally{if(mine===generation)detail.inert=false;}
  }
  workspace.addEventListener('click',e=>{
    const add=e.target.closest('[data-access-add]');
    if(add){
      const form=add.closest('form'),picker=form.querySelector('[data-access-picker]'),selected=picker.options[picker.selectedIndex];
      if(!picker.value||selected.disabled){status.textContent='추가할 그룹을 선택하세요.';return;}
      const row=document.createElement('div');row.className='access-group-card';row.dataset.accessRow='';row.dataset.original='none';
      const name=document.createElement('strong');name.textContent=selected.textContent;
      const select=document.createElement('select');select.name='group_'+picker.value;select.dataset.accessRole='';select.setAttribute('aria-label',selected.textContent+' 권한');
      for(const[value,label]of[['read','보기 가능'],['write','편집 가능'],['none','추가 취소']]){const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);}
      row.append(name,select);form.querySelector('[data-access-cards]').append(row);form.querySelector('[data-access-empty]')?.remove();selected.disabled=true;picker.value='';status.textContent='그룹을 추가했습니다. 권한을 선택한 뒤 저장하세요.';refresh();select.focus();return;
    }
    const all=e.target.closest('[data-select-visible]');
    if(all){all.closest('form').querySelectorAll('[data-search-row]:not([hidden]) input[name=add]').forEach(input=>{input.checked=true;input.closest('label').classList.add('permission-changed');});return;}
    const link=e.target.closest('a[data-permission-nav]');if(!link||e.ctrlKey||e.metaKey||e.shiftKey||e.button!==0)return;e.preventDefault();panel(new URL(link.href));
  });
  detail.addEventListener('change',refresh);
  detail.addEventListener('input',e=>{
    if(e.target.matches('[data-row-search],[data-department-filter]')){const scope=e.target.closest('form')||detail,query=(scope.querySelector('[data-row-search]')?.value||'').trim().toLocaleLowerCase(),department=scope.querySelector('[data-department-filter]')?.value||'';scope.querySelectorAll('[data-search-row]').forEach(row=>{row.hidden=!row.dataset.searchRow.toLocaleLowerCase().includes(query)||(!!department&&row.dataset.department!==department);});}
    if(e.target.matches('input[name=add],input[name=remove]'))e.target.closest('label').classList.toggle('permission-changed',e.target.checked);
  });
  detail.addEventListener('submit',e=>{const form=e.target.closest('[data-panel-filter]');if(!form)return;e.preventDefault();const url=new URL(location.href);url.search=new URLSearchParams(new FormData(form)).toString();panel(url);});
  library.addEventListener('change',e=>{if(dirty()&&!confirm('저장하지 않은 변경이 있습니다. 이동할까요?')){e.target.value=library.dataset.drive;return;}location.href=`/work/shared-documents/${encodeURIComponent(e.target.value)}/root/permissions/`;});
  window.addEventListener('popstate',()=>panel(new URL(location.href),false));refresh();
})();
