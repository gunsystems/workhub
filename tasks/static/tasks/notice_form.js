(() => {
  const audience=document.getElementById('id_audience'), timing=document.getElementById('id_timing');
  if (!audience || !timing) return;
  function update() {
    document.getElementById('notice-teams').hidden=audience.value!=='team';
    document.getElementById('notice-employees').hidden=audience.value!=='person';
    document.getElementById('notice-schedule').hidden=timing.value!=='scheduled';
    document.getElementById('id_scheduled_for').required=timing.value==='scheduled';
  }
  audience.addEventListener('change',update);timing.addEventListener('change',update);update();
})();
