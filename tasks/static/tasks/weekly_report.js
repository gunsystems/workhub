(() => {
 const kind=document.getElementById('id_report_type');
 const section=document.querySelector('[data-next-week-section]');
 const summary=document.querySelector('label[for="id_summary"]');
 if(!kind||!section)return;
 const update=()=>{section.hidden=kind.value!=='weekly';if(summary){const node=Array.from(summary.childNodes).find(n=>n.nodeType===3);if(node)node.textContent=kind.value==='weekly'?'이번 주 진행 업무: ':'수행 결과 · 장애 사항 · 다음 계획: ';}};
 kind.addEventListener('change',update);update();
})();
