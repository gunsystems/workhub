(()=>{
  'use strict';
  const form=document.querySelector('.profile-address-form');
  if(!form)return;
  const search=form.querySelector('[data-postcode-search]'),panel=form.querySelector('[data-postcode-panel]'),host=form.querySelector('[data-postcode-embed]'),status=form.querySelector('[data-postcode-status]');
  const code=form.querySelector('[name=postal_code]'),address=form.querySelector('[name=address]'),detail=form.querySelector('[name=detail]');
  if(!search||!panel||!host||!code||!address||!detail)return;
  let loading=null,generation=0;
  const sdk=()=>window.kakao?.Postcode;
  function load(){
    if(sdk())return Promise.resolve(sdk());
    if(loading)return loading;
    loading=new Promise((resolve,reject)=>{
      const script=document.createElement('script');let settled=false;
      const finish=ok=>{if(settled)return;settled=true;window.clearTimeout(timer);if(ok&&sdk())resolve(sdk());else{script.remove();reject(new Error('postcode unavailable'))}};
      const timer=window.setTimeout(()=>finish(false),12000);
      script.src='https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js';script.async=true;
      script.onload=()=>finish(true);script.onerror=()=>finish(false);document.head.appendChild(script);
    }).catch(error=>{loading=null;throw error});
    return loading;
  }
  function close(focus=true){generation++;panel.hidden=true;host.replaceChildren();status.textContent='';search.setAttribute('aria-expanded','false');search.disabled=false;if(focus)search.focus()}
  form.querySelector('[data-postcode-close]').addEventListener('click',()=>close());
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close()}});
  search.addEventListener('click',async()=>{
    const token=++generation;panel.hidden=false;search.disabled=true;search.setAttribute('aria-expanded','true');status.textContent='주소 검색을 불러오는 중입니다.';
    try{
      const Postcode=await load();if(token!==generation||panel.hidden)return;
      host.replaceChildren();host.style.height='450px';
      new Postcode({width:'100%',height:'100%',minWidth:0,maxSuggestItems:5,
        onresize:size=>{if(token===generation&&Number.isFinite(size.height))host.style.height=Math.max(400,Math.min(5000,size.height))+'px'},
        oncomplete:data=>{
          if(token!==generation||panel.hidden)return;
          const zone=String(data.zonecode||'');let base=String((data.userSelectedType==='J'?data.jibunAddress:data.roadAddress)||data.address||'').trim();
          if(data.userSelectedType==='R'){
            const extra=[];if(data.bname&&/[동로가]$/.test(data.bname))extra.push(String(data.bname));if(data.apartment==='Y'&&data.buildingName&&!extra.includes(data.buildingName))extra.push(String(data.buildingName));
            if(extra.length)base+=' ('+extra.join(', ')+')';
          }
          if(!/^[0-9]{5}$/.test(zone)||!base||base.length>255){status.textContent='선택한 주소를 확인하지 못했습니다. 다시 검색하거나 직접 입력해 주세요.';return}
          if(address.value!==base)detail.value='';code.value=zone;address.value=base;
          code.dispatchEvent(new Event('input',{bubbles:true}));address.dispatchEvent(new Event('input',{bubbles:true}));close(false);detail.focus();
        }
      }).embed(host,{autoClose:true});status.textContent='도로명·건물명·지번으로 검색하세요.';
    }catch(_){if(token===generation)status.textContent='주소 검색을 불러오지 못했습니다. 다시 누르거나 직접 입력해 주세요.'}
    finally{search.disabled=false}
  });
})();
