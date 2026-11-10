(() => {
  const kind=document.getElementById('id_request_kind'),team=document.getElementById('id_department');
  if(!kind||!team)return;
  function update(){
    const support=kind.value==='support';
    for(const name of ['customer','solution','requested_start_date','response_due_date']){
      const field=document.getElementById('id_'+name);if(!field)continue;
      field.required=support;
      const label=field.closest('.workspace-field')?.querySelector('label');
      if(label){let badge=label.querySelector('[data-support-required]');if(!badge){badge=document.createElement('span');badge.className='required-badge';badge.dataset.supportRequired='1';badge.textContent='필수';label.append(' ',badge);}badge.hidden=!support;}
    }
    for(const name of ['coordinator','preferred_employee']){
      const field=document.getElementById('id_'+name);if(!field)continue;
      for(const option of field.options){const outside=!!option.value&&option.dataset.team!==team.value;option.hidden=outside;option.disabled=outside;}
      if(field.selectedOptions[0]?.disabled)field.value='';
    }
  }
  kind.addEventListener('change',update);team.addEventListener('change',update);update();
})();