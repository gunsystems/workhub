document.addEventListener('DOMContentLoaded',()=>{
 const form=document.querySelector('form[data-content-preview]'),dialog=document.getElementById('content-preview'),body=document.getElementById('content-preview-body');
 if(!form||!dialog||!body)return;
 const text=(tag,value,cls='')=>{const el=document.createElement(tag);el.textContent=value;el.className=cls;return el;};
 const value=name=>form.elements.namedItem(name)?.value||'';
 const checked=name=>Boolean(form.elements.namedItem(name)?.checked);
 const selected=name=>{const el=form.elements.namedItem(name);return el?.selectedOptions?.[0]?.textContent?.trim()||'';};
 const checkedLabels=name=>[...form.querySelectorAll(`input[name="${name}"]:checked`)].map(el=>el.labels?.[0]?.textContent?.trim()||el.value).join(', ');
 const line=(label,content)=>body.append(text('p',label+' '+content,'preview-meta'));
 let opener;
 function show(button){
  opener=button;body.replaceChildren();const survey=form.dataset.contentPreview==='survey';dialog.classList.toggle('survey-preview',survey);
  document.getElementById('content-preview-heading').textContent=survey?'설문 미리보기':'공지 미리보기';
  body.append(text('h1',value('title')||'제목을 입력해 주세요.'));
  const audience=value('audience');line('전달 대상:',audience==='all'?'전사':checkedLabels(audience==='team'?'departments':'employees')||'아직 선택하지 않았습니다.');
  const channels=[survey?'메일의 설문 응답 링크':'Workhub 공지사항'];if(!survey&&checked('send_email'))channels.push('이메일');if(checked('send_messenger'))channels.push('하이웍스 알림');line('전달 방법:',channels.join(' · '));
  if(survey){
   line('응답 방식:',checked('anonymous')?'익명 응답':'기명 응답');line('응답 기간:',(value('starts_at')||'시작 시각 미지정').replace('T',' ')+' ~ '+(value('closes_at')||'마감 시각 미지정').replace('T',' ')+' · 한국 시간');
   body.append(text('p',value('description'),'preview-copy'));
   let number=0;
   for(const fieldset of form.querySelectorAll('#survey-questions fieldset')){
    const label=fieldset.querySelector('input[name$="-label"]')?.value?.trim();if(!label||fieldset.querySelector('input[name$="-DELETE"]')?.checked)continue;
    const section=document.createElement('section');section.className='preview-question';const required=fieldset.querySelector('input[name$="-required"]')?.checked;
    section.append(text('h3',`${++number}. ${label}${required?' · 필수':''}`));
    const kind=fieldset.querySelector('select[name$="-kind"]')?.value;
    if(kind==='single'||kind==='multiple'){
     const options=(fieldset.querySelector('textarea[name$="-options"]')?.value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
     for(const option of options){section.append(text('p',(kind==='single'?'○ ':'□ ')+option,'preview-choice'));}
     if(!options.length)section.append(text('p','선택지를 입력해 주세요.','preview-meta'));
    }else if(kind==='rating'){section.append(text('p','①　②　③　④　⑤','preview-rating'));}
    else if(kind==='text'){section.append(text('div','자유롭게 의견을 작성하는 영역입니다.','preview-answer'));}
    else{section.append(text('p','응답 방식을 선택해 주세요.','preview-meta'));}
    body.append(section);
   }
   if(!number)body.append(text('p','질문을 작성하면 이곳에 표시됩니다.','preview-meta'));
  }else{
   line('발송 시점:',value('timing')==='scheduled'?(value('scheduled_for')||'예약 시각 미지정').replace('T',' ')+' · 한국 시간':'발송 확정 후 즉시');
   body.append(text('div',value('body')||'공지 내용을 입력해 주세요.','preview-copy'));
  }
  if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
 }
 for(const button of document.querySelectorAll('[data-content-preview-open]'))button.addEventListener('click',()=>show(button));
 for(const button of dialog.querySelectorAll('[data-preview-close]'))button.addEventListener('click',()=>{dialog.close();opener?.focus();});
 dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
});
