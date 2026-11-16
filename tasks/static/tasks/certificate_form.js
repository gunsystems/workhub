/* Keep the custom name available only for other document requests. */
(()=>{'use strict';
  const kind=document.getElementById('id_kind'),name=document.getElementById('id_document_name');
  if(!kind||!name)return;
  const sync=()=>{const other=kind.value==='other';name.disabled=!other;name.required=other;};
  kind.addEventListener('change',sync);sync();
})();
