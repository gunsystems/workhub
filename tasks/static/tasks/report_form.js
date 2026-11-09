(() => {
  const form=document.querySelector('[data-report-today]');
  if (!form) return;
  const kind=document.getElementById('id_report_type'), start=document.getElementById('id_period_start'), end=document.getElementById('id_period_end');
  const button=document.getElementById('report-default-period');
  const parts=form.dataset.reportToday.split('-').map(Number);
  const today=new Date(Date.UTC(parts[0],parts[1]-1,parts[2]));
  const iso=d=>`${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  let customized=false;
  [start,end].forEach(field=>['input','change'].forEach(event=>field.addEventListener(event,()=>{customized=true;}))); 
  function apply() {
    let first=new Date(today), last=new Date(today);
    if (kind.value==='weekly') {
      first.setUTCDate(first.getUTCDate()-(first.getUTCDay()+6)%7);
      last=new Date(first);last.setUTCDate(last.getUTCDate()+6);
    } else if (kind.value==='monthly') {
      first=new Date(Date.UTC(parts[0],parts[1]-1,1));last=new Date(Date.UTC(parts[0],parts[1],0));
    } else if (kind.value==='period') return;
    start.value=iso(first);end.value=iso(last);
  }
  kind.addEventListener('change',()=>{
    button.disabled=kind.value==='period';
    if (form.dataset.newReport==='true' && !customized) apply();
  });
  button.addEventListener('click',apply);button.disabled=kind.value==='period';
})();
