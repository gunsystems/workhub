(() => {
  'use strict';
  const root=document.querySelector('[data-permission-report]');
  if(!root)return;
  const tree=root.querySelector('[data-report-tree]'),status=root.querySelector('[data-report-status]');
  const search=root.querySelector('[data-report-search]'),refresh=root.querySelector('[data-report-refresh]');
  const updated=root.querySelector('[data-report-updated]');
  let known='',timer,busy=false;
  const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
  const date=value=>new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
  function filter(){
    const q=search.value.trim().toLocaleLowerCase();
    function visit(n){const own=n._search.toLocaleLowerCase().includes(q);const children=Array.from(n._children.children).map(visit);
      n.hidden=!!q&&!own&&!children.some(Boolean);if(q&&!n.hidden)n.open=true;return !n.hidden;}
    const found=Array.from(tree.children).map(visit).some(Boolean);root.querySelector('[data-report-empty]').hidden=found||!q;
  }
  function paint(payload){
    const opened=new Map(Array.from(tree.querySelectorAll('details')).map(n=>[n.dataset.key,n.open]));
    tree.replaceChildren();const nodes=new Map();
    for(const data of payload.nodes||[]){
      const n=el('details','','report-node');n.dataset.key=data.key;n.open=opened.get(data.key)??true;
      const head=el('summary');head.append(el('strong',data.name),el('span',data.unique?'별도 설정':'상위 권한 사용','report-badge'));
      const body=el('div','','report-body');n._children=el('div','','report-children');n._search=data.name;
      body.append(el('p','권한 원본: '+data.source,'report-source'));const list=el('ul','','report-grants');
      for(const grant of data.grants){
        const li=el('li','','report-grant');li.append(el('strong',grant.name+' · '+grant.role));
        let text;
        if(grant.kind==='group')text=grant.members.length?grant.members.map(m=>(m.name||m.email||'이름 없음')+(m.kind!=='person'?' (그룹·기타 대상: 구성원 별도 확인)':'')).join(' · '):'구성원 없음';
        else text=grant.kind==='person'?'개인 직접 권한'+(grant.email?' · '+grant.email:''):'그룹·특수 대상: 구성원 별도 확인';
        li.append(el('span',text,'report-members'));list.append(li);n._search+=' '+grant.name+' '+grant.role+' '+text+' '+(grant.members||[]).map(m=>m.email).join(' ');
      }
      body.append(list);const link=el('a','접근 관리','report-edit');link.href=data.manage_url;body.append(link,el('div','확인: '+date(data.at),'report-stamp'));
      n.append(head,body,n._children);nodes.set(data.key,n);(data.parent?nodes.get(data.parent)._children:tree).append(n);
    }
    filter();
  }
  async function request(url,body){
    const options={credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}};
    if(body){options.method='POST';options.headers['X-CSRFToken']=root.querySelector('[name=csrfmiddlewaretoken]').value;options.body=body;}
    const r=await fetch(url,options);
    if(!r.ok||!r.headers.get('content-type')?.includes('application/json')){const e=Error('현황을 확인하지 못했습니다. 관리자 연결을 확인하세요.');e.clear=[400,401,403].includes(r.status)||r.redirected;throw e;}
    return r.json();
  }
  function names(data){
    const box=root.querySelector('[data-report-name-links]'),rows=root.querySelector('[data-report-name-rows]');
    rows.replaceChildren();box.hidden=!data.unmatched?.length;root.querySelector('[data-report-name-count]').textContent=data.unmatched?.length?'· '+data.unmatched.length+'명':'';
    for(const person of data.unmatched||[]){
      const form=el('form','','report-name-form');form.append(el('strong',person.name+' · '+person.identity));
      const select=el('select');select.setAttribute('aria-label',person.name+' 직원 연결');select.required=true;
      const empty=el('option','직원을 선택하세요');empty.value='';select.append(empty);
      for(const staff of data.directory){const option=el('option',staff.name+' · '+staff.email);option.value=staff.email;select.append(option);}
      const button=el('button','이름 연결','button');button.type='submit';form.append(select,button);rows.append(form);
      form.addEventListener('submit',async e=>{e.preventDefault();if(!select.value)return;button.disabled=true;
        try{const result=await request(root.dataset.namesUrl,new URLSearchParams({principal:person.id,email:select.value}));apply(result);}
        catch(error){status.textContent=error.message;button.disabled=false;}
      });
    }
  }
  function apply(data){
    if(data.payload){paint(data.payload);names(data);}known=data.at||'';
    const count=tree.querySelectorAll('details').length;
    updated.textContent=known?'마지막 동기화: '+date(known)+' · 관리자 현황용 저장 정보':'아직 저장된 현황이 없습니다. 처음 한 번은 동기화가 필요합니다.';
    status.textContent=data.pending?`백그라운드 동기화 중 · ${data.progress}곳 확인${known?' · 저장된 현황을 계속 표시합니다.':''}`:
      data.error||(known?`저장된 현황 · 전체 ${count}곳 표시`:'동기화가 필요합니다.');
    refresh.disabled=data.pending;clearTimeout(timer);timer=setTimeout(poll,data.pending?3000:60000);
  }
  async function poll(){
    if(busy)return;busy=true;
    try{const url=new URL(root.dataset.statusUrl,location.origin);url.search=new URLSearchParams({known});apply(await request(url));}
    catch(e){if(e.clear){tree.replaceChildren();known='';}status.textContent='최신 정보 확인 필요 · '+e.message;refresh.disabled=false;timer=setTimeout(poll,60000);}
    finally{busy=false;}
  }
  refresh.addEventListener('click',async()=>{if(busy)return;busy=true;refresh.disabled=true;clearTimeout(timer);
    try{apply(await request(root.dataset.refreshUrl,new URLSearchParams({refresh:'1'})));}
    catch(e){status.textContent='최신 정보 확인 필요 · 기존 현황을 유지합니다.';refresh.disabled=false;timer=setTimeout(poll,60000);}
    finally{busy=false;}
  });
  search.addEventListener('input',filter);
  for(const [selector,open]of [['[data-report-expand]',true],['[data-report-collapse]',false]])root.querySelector(selector).addEventListener('click',()=>tree.querySelectorAll('details').forEach(n=>{n.open=open;}));
  apply(JSON.parse(document.getElementById('permission-report-initial').textContent));
})();
