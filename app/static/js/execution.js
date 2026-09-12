(() => {
 const N=window.Narsika,{$}=N;let current=null,timer=null;const terminal=$('#terminal-output');
 function render(run){current=run;if($('#job-status'))$('#job-status').textContent=run.status;if(terminal){terminal.textContent=run.output||'Waiting for the operation worker…';terminal.scrollTop=terminal.scrollHeight;}if($('#job-detail'))$('#job-detail').textContent=`Run #${run.id} · ${run.kind} · ${run.progress}%${run.error_code?' · '+run.error_code:''}`;if($('#cancel-job'))$('#cancel-job').disabled=!['PENDING','RUNNING'].includes(run.status)||!N.allowed('operate');
  let artifacts=$('#run-artifacts');if(!artifacts&&terminal){artifacts=document.createElement('div');artifacts.id='run-artifacts';artifacts.className='panel-body';terminal.after(artifacts);}if(artifacts)artifacts.innerHTML=(N.allowed('operate')?(run.artifacts||[]):[]).map(a=>`<a class="btn" href="/api/artifacts/${a.id}/download">${N.icon('download')}${N.h(a.name)}</a>`).join('');
 }
 async function poll(id){clearTimeout(timer);try{const {data}=await N.apiFetch('/api/automation/runs/'+id);if(current&&current.id!==id)return;render(data);if(['PENDING','RUNNING'].includes(data.status))timer=setTimeout(()=>poll(id),1200);else window.dispatchEvent(new CustomEvent('narsika:run-finished',{detail:data}));}catch(ex){N.toast(ex.message);if(current?.id===id)timer=setTimeout(()=>poll(id),4000);}}
 async function watch(id){clearTimeout(timer);current={id};await poll(id);}
 async function start(body){if(!N.guard())return;const {data}=await N.apiFetch('/api/automation/runs',{method:'POST',body});await watch(data.run_id);return data;}
 if($('#cancel-job')){$('#cancel-job').disabled=true;$('#cancel-job').onclick=()=>{if(current)N.confirm('Cancel operation','Commands already applied remain on the device. Cancellation stops subsequent execution where possible.',async()=>{await N.apiFetch('/api/automation/runs/'+current.id+'/cancel',{method:'POST',body:{}});await poll(current.id);});};}
 if($('#download-log'))$('#download-log').onclick=()=>N.download('narsika-run-'+(current?.id||'none')+'.log',terminal?.textContent||'');
 window.addEventListener('pagehide',()=>clearTimeout(timer));window.NarsikaExecution={start,watch};
})();
