const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function harness(){
  let active='',opts,embeds=0;const scripts=[];
  const el=name=>({value:'',textContent:'',hidden:true,disabled:false,style:{},handlers:{},attrs:{},addEventListener(type,fn){this.handlers[type]=fn},setAttribute(k,v){this.attrs[k]=v},focus(){active=name},replaceChildren(){},dispatchEvent(){},remove(){this.removed=true}});
  const search=el('search'),panel=el('panel'),host=el('host'),status=el('status'),code=el('code'),address=el('address'),detail=el('detail'),close=el('close');
  const map={'[data-postcode-search]':search,'[data-postcode-panel]':panel,'[data-postcode-embed]':host,'[data-postcode-status]':status,'[name=postal_code]':code,'[name=address]':address,'[name=detail]':detail,'[data-postcode-close]':close};
  const form={querySelector:s=>map[s]};const window={setTimeout:()=>1,clearTimeout(){}};const document={querySelector:()=>form,createElement:()=>el('script'),head:{appendChild:s=>scripts.push(s)}};
  vm.runInNewContext(fs.readFileSync('tasks/static/tasks/profile_postcode.js','utf8'),{document,window,Event:class{}});
  const ready=()=>{window.kakao={Postcode:class{constructor(o){opts=o}embed(h,o){assert.equal(h,host);assert.equal(o.autoClose,true);embeds++}}}};
  return{search,panel,host,status,code,address,detail,close,scripts,ready,get opts(){return opts},get embeds(){return embeds},get active(){return active}};
}
(async()=>{
  let h=harness();assert.equal(h.scripts.length,0);h.address.value='기존 비공개 주소';h.detail.value='기존 상세';let opened=h.search.handlers.click();assert.equal(h.scripts.length,1);assert.equal(h.scripts[0].src,'https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js');assert.equal(h.search.disabled,true);h.ready();h.scripts[0].onload();await opened;assert.equal(h.embeds,1);assert.equal(h.opts.minWidth,0);assert.equal(h.opts.width,'100%');assert.equal('q' in h.opts,false);assert.equal('query' in h.opts,false);
  h.opts.oncomplete({zonecode:'12345',userSelectedType:'R',roadAddress:'검증로 1',bname:'검증동',buildingName:'검증아파트',apartment:'Y'});assert.equal(h.code.value,'12345');assert.equal(h.address.value,'검증로 1 (검증동, 검증아파트)');assert.equal(h.detail.value,'');assert.equal(h.active,'detail');assert.equal(h.panel.hidden,true);
  h.detail.value='유지할 상세';await h.search.handlers.click();h.opts.oncomplete({zonecode:'12345',userSelectedType:'R',roadAddress:'검증로 1',bname:'검증동',buildingName:'검증아파트',apartment:'Y'});assert.equal(h.detail.value,'유지할 상세');assert.equal(h.scripts.length,1);
  await h.search.handlers.click();h.opts.oncomplete({zonecode:'12345',userSelectedType:'J',jibunAddress:'검증동 1',buildingName:'제외되는 참고'});assert.equal(h.address.value,'검증동 1');
  await h.search.handlers.click();const previous=h.address.value;h.opts.oncomplete({zonecode:'bad',userSelectedType:'R',roadAddress:'잘못된 결과'});assert.equal(h.address.value,previous);assert.match(h.status.textContent,/직접 입력/);h.opts.onresize({height:700});assert.equal(h.host.style.height,'700px');h.close.handlers.click();assert.equal(h.active,'search');assert.equal(h.panel.hidden,true);
  h=harness();opened=h.search.handlers.click();h.close.handlers.click();assert.equal(h.search.disabled,false);h.ready();h.scripts[0].onload();await opened;assert.equal(h.embeds,0);assert.equal(h.panel.hidden,true);
  h=harness();h.address.value='보존 주소';opened=h.search.handlers.click();h.scripts[0].onerror();await opened;assert.equal(h.search.disabled,false);assert.match(h.status.textContent,/직접 입력/);assert.equal(h.address.value,'보존 주소');opened=h.search.handlers.click();assert.equal(h.scripts.length,2);h.ready();h.scripts[1].onload();await opened;assert.equal(h.embeds,1);
  console.log('Postcode lazy loading, road/jibun selection, private input preservation, cancellation, validation and retry checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
