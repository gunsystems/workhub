/* Only move an invalid/missing end forwards; preserve a user's later end date. */
(()=>{'use strict';
  const pairs=[['start_date','due_date'],['start','end'],['activity_start','activity_end'],['requested_start_date','requested_due_date']];
  function sync(form,startName,endName){
    const start=form.querySelector(`[name="${startName}"]`),end=form.querySelector(`[name="${endName}"]`);
    if(!start||!end)return;
    if(!start.value){end.min='';return;}
    end.min=start.value;
    if(!end.value||end.value<start.value||(start.type==='datetime-local'&&end.value===start.value)){
      end.value=start.type==='datetime-local'?new Date(Date.parse(start.value+'Z')+3600000).toISOString().slice(0,16):start.value;
      end.dispatchEvent(new Event('change',{bubbles:true}));
    }
  }
  document.addEventListener('change',event=>{
    const field=event.target,form=field.form;if(!form)return;
    for(const [start,end] of pairs)if(field.name===start)sync(form,start,end);
  });
  for(const form of document.forms)for(const [startName,endName] of pairs){
    const start=form.querySelector(`[name="${startName}"]`),end=form.querySelector(`[name="${endName}"]`);
    if(start&&end&&start.value)end.min=start.value;
  }
})();
