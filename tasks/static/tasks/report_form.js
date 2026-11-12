(() => {
  const form=document.querySelector('[data-report-today]');
  if (!form) return;
  const kind=document.getElementById('id_report_type'), start=document.getElementById('id_period_start'), end=document.getElementById('id_period_end');
  if (!kind||!start||!end) return;
  const fields=['summary','next_week_plan','report_issues'].map(name=>document.getElementById('id_'+name));
  const checks=Array.from(form.querySelectorAll('input[name="tasks"]'));
  const applied=document.getElementById('calendar-applied');
  const panel=document.querySelector('[data-period-calendar]');
  const guide=document.querySelector('[data-weekly-guide]');
  const heading=document.querySelector('[data-report-heading]');
  const intro=document.querySelector('[data-report-intro]');
  const fresh=form.dataset.newReport==='true', drafts=new Map();
  const parts=form.dataset.reportToday.split('-').map(Number),today=new Date(Date.UTC(parts[0],parts[1]-1,parts[2]));
  const iso=d=>`${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
  function defaults(value) {
    if(value==='period')return ['',''];
    let first=new Date(today),last=new Date(today);
    if(value==='weekly'){first.setUTCDate(first.getUTCDate()-(first.getUTCDay()+6)%7);last=new Date(first);last.setUTCDate(last.getUTCDate()+6);}
    else if(value==='monthly'){first=new Date(Date.UTC(parts[0],parts[1]-1,1));last=new Date(Date.UTC(parts[0],parts[1],0));}
    return [iso(first),iso(last)];
  }
  const capture=()=>({text:fields.map(field=>field?.value||''),tasks:checks.filter(field=>field.checked).map(field=>field.value),dates:[start.value,end.value],applied:applied?.value||'0'});
  let previous=kind.value;
  function update(){
    if(panel)panel.hidden=kind.value!=='period';
    if(guide)guide.hidden=kind.value!=='weekly';
    if(heading)heading.textContent=kind.value==='weekly'?'주간업무보고 작성':'업무보고 작성';
    if(intro)intro.textContent=kind.value==='weekly'?'이번 주 진행 업무와 다음 주 예정 업무를 작성하세요.':(kind.value==='period'?'보고 기간을 선택하고 내용을 작성하세요. 필요하면 해당 기간의 업무를 불러올 수 있습니다.':'특정 업무나 사안에 대한 내용을 직접 작성하세요.');
  }
  kind.addEventListener('change',()=>{
    if(fresh){
      drafts.set(previous,capture());
      const next=drafts.get(kind.value)||{text:['','',''],tasks:[],dates:defaults(kind.value),applied:'0'};
      fields.forEach((field,index)=>{if(field)field.value=next.text[index];});
      checks.forEach(field=>{field.checked=next.tasks.includes(field.value);});
      [start.value,end.value]=next.dates;
      if(applied)applied.value=next.applied;
    }
    previous=kind.value;update();
  });
  const apply=document.querySelector('[data-calendar-apply]');
  apply?.addEventListener('click',()=>{
    if(kind.value!=='period')return;
    const preview=document.querySelector('[data-calendar-preview]');
    if(!preview||!fields[0])return;
    fields[0].value=preview.value;
    const ids=Array.from(document.querySelectorAll('[data-calendar-task]')).map(field=>field.value);
    checks.forEach(field=>{field.checked=ids.includes(field.value);});
    if(applied)applied.value='1';
  });
  form.addEventListener('submit',event=>{
    if(event.submitter?.value!=='calendar')return;
    const file=form.querySelector('input[type="file"]');
    if(file?.files?.length){event.preventDefault();const warning=document.querySelector('[data-calendar-file-warning]');if(warning)warning.hidden=false;}
  });
  update();
})();
