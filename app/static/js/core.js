(() => {
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const D=window.NarsikaData;
  const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const paths={grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',server:'M3 3h18v7H3z M3 14h18v7H3z M6 6h1 M6 17h1 M10 6h8 M10 17h8',search:'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',pulse:'M2 12h4l3-8 5 16 3-8h5',ports:'M3 7h18v12H3z M7 7V4h10v3 M7 11v4 M12 11v4 M17 11v4',terminal:'M4 6l6 6-6 6 M13 18h7',layers:'M2 8l10-6 10 6-10 6z M2 12l10 6 10-6 M2 16l10 6 10-6',shield:'M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6z M8 12l3 3 5-6',backup:'M3 5h18v14H3z M3 10h18 M10 14h4',clock:'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M12 6v6l4 2',settings:'M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2 M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',plus:'M12 5v14 M5 12h14',arrow:'M5 12h14 M13 6l6 6-6 6',download:'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',upload:'M12 16V3 M7 8l5-5 5 5 M4 16v5h16v-5',refresh:'M20 7v5h-5 M4 17v-5h5 M19 7a8 8 0 0 0-14-2 M5 17a8 8 0 0 0 14 2',more:'M5 12h1 M11 12h1 M17 12h1',close:'M5 5l14 14 M5 19L19 5',check:'M4 12l5 5L20 6',edit:'M16 3l5 5-12 12H4v-5z M13 6l5 5',trash:'M3 6h18 M8 6V3h8v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',help:'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M9 8a3 3 0 0 1 6 0c0 3-3 2-3 5 M12 17h.1',play:'M7 3l14 9-14 9z',stop:'M5 5h14v14H5z',code:'M7 5l-6 7 6 7 M17 5l6 7-6 7 M14 3l-4 18',logout:'M9 3H3v18h6 M9 12h13 M16 6l6 6-6 6',eye:'M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',copy:'M8 8h13v13H8z M16 8V3H3v13h5'};
  const icon=(name)=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.grid}"/></svg>`;
  const icons=(root=document)=>$$('[data-icon]',root).forEach(e=>{e.innerHTML=icon(e.dataset.icon);e.removeAttribute('data-icon');});
  const badge=(s)=>`<span class="badge ${['online','offline','degraded','unknown','success','failed','verified','running','demo','cisco','mikrotik'].includes(String(s).toLowerCase())?String(s).toLowerCase():''}">${['online','offline','degraded','unknown'].includes(s)?'<i class="dot"></i>':''}${h(s)}</span>`;
  const validIP=s=>/^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$/.test(s)&&s.split('.').every(n=>+n<256);
  const validPort=s=>/^\d+$/.test(String(s))&&+s>=1&&+s<=65535;
  const toast=(msg)=>{const box=$('#toasts');if(!box)return;const el=document.createElement('div');el.className='toast';el.textContent=msg;box.append(el);setTimeout(()=>el.remove(),5000);};
  const modal=(title,body,setup)=>{const el=$('#dialog');if(!el)return;el.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${h(title)}</h2><button class="btn icon-only ghost" data-close aria-label="Close">${icon('close')}</button></div><div class="dialog-body">${body}</div>`;$('[data-close]',el).onclick=()=>el.close();if(!el.open)el.showModal();setup?.(el);};
  const confirm=(title,description,fn)=>modal(title,`<p class="muted">${h(description)}</p><div class="dialog-actions"><button class="btn" id="cancel-confirm">Cancel</button><button class="btn primary" id="accept-confirm">Confirm</button></div>`,el=>{$('#cancel-confirm',el).onclick=()=>el.close();$('#accept-confirm',el).onclick=()=>{el.close();Promise.resolve().then(fn).catch(e=>toast(e.message));};});
  const error=(form,msg)=>{let e=$('[data-error]',form);if(!e){e=document.createElement('p');e.className='notice error';e.dataset.error='';e.setAttribute('role','alert');form.prepend(e);}e.textContent=msg;};
  const allowed=(level='operate')=>level==='read'||D.state.settings.role==='ADMIN'||level==='operate'&&D.state.settings.role==='OPERATOR';
  const guard=(level='operate')=>{if(allowed(level))return true;toast(`The ${D.state.settings.role} role cannot perform this action.`);return false;};
  const permissions=()=>$$('[data-permission]').forEach(e=>{e.disabled=!allowed(e.dataset.permission);e.title=e.disabled?'Unavailable for the current role':'';});
  const download=(name,text,type='text/plain')=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
  const csv=rows=>rows.map(row=>row.map(v=>{let s=String(v??'');if(/^[=+@\-\t\r]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}).join(',')).join('\r\n');
  const device=id=>D.state.devices.find(d=>d.id===Number(id));
  const options=(vendor)=>D.state.devices.filter(d=>!vendor||d.vendor===vendor).map(d=>`<option value="${d.id}">${h(d.name)} · ${h(d.ip)}</option>`).join('');
  const empty=(title='No results',text='Try changing your filters.')=>`<div class="empty">${icon('search')}<h3>${h(title)}</h3><p>${h(text)}</p></div>`;
  const meter=(n,cls='blue')=>n==null?'<span class="muted">N/A</span>':`<span class="meter ${cls}"><span class="meter-track"><i style="width:${Math.max(0,Math.min(100,+n))}%"></i></span>${h(n)}%</span>`;
  const stamp=n=>new Date(n).toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short'});
  const chart=(values,color='blue')=>{if(values.filter(Number.isFinite).length<2)return empty('Waiting for samples','Two real samples are required to show a trend.');let drawing='',continuing=false;values.forEach((v,i)=>{if(!Number.isFinite(v)){continuing=false;return;}drawing+=(continuing?' L':' M')+(i*600/Math.max(1,values.length-1))+','+(150-Math.max(0,Math.min(100,v))*1.4);continuing=true;});return `<svg class="chart" viewBox="0 0 600 160" preserveAspectRatio="none" role="img" aria-label="Observed ${color==='blue'?'CPU':'memory'} samples; unavailable samples leave gaps"><path class="chart-grid" d="M0 10H600 M0 55H600 M0 100H600 M0 145H600"/><path class="chart-line" style="stroke:var(--${color});fill:none" d="${drawing}"/></svg>`;};
  async function apiFetch(path,{method='GET',body,signal}={}) {
    const token=$('meta[name=csrf-token]')?.content;
    const hasBody=body!==undefined;const multipart=body instanceof FormData;
    const response=await fetch(path,{method,signal,credentials:'same-origin',headers:{Accept:'application/json',...(hasBody&&!multipart?{'Content-Type':'application/json'}:{}),...(token?{'X-CSRFToken':token}:{})},body:hasBody?(multipart?body:JSON.stringify(body)):undefined});
    if(response.status===204&&response.ok)return null;
    let result;
    try{result=await response.json();}catch{throw new Error('The server returned an invalid response.');}
    if(response.status===401&&!['login','change-password'].includes(document.body.dataset.page))location.href='/login';
    if(result.error?.code==='PASSWORD_CHANGE_REQUIRED')location.href='/change-password';
    if(!response.ok||result.error) {
      const failure=new Error(result.error?.message||`Request failed (${response.status}).`);
      failure.code=result.error?.code;
      failure.fields=result.error?.fields||{};
      failure.request_id=result.error?.request_id;
      throw failure;
    }
    return result;
  }
  window.Narsika={$,$$,D,h,icon,icons,badge,validIP,validPort,toast,modal,confirm,error,allowed,guard,permissions,download,csv,device,options,empty,meter,stamp,chart,apiFetch};
  const page=document.body.dataset.page;
  $$('.nav-link').forEach(a=>{if(a.dataset.page===page){a.classList.add('active');a.setAttribute('aria-current','page');}});
  const refresh=()=>{const user=D.state.user;if($('#account-name'))$('#account-name').textContent=user?.name||'User';if($('#account-avatar'))$('#account-avatar').textContent=(user?.name||'User').split(' ').map(s=>s[0]).join('').slice(0,2);document.body.classList.toggle('compact',!!D.state.settings.compact);if($('#workspace-name'))$('#workspace-name').textContent=D.state.settings.workspace;if($('#role-label'))$('#role-label').textContent=user?.role||'VIEWER';if($('#device-count'))$('#device-count').textContent=D.state.devices.length;permissions();};
  const palette=()=>modal('Go to…','<input id="palette-query" type="search" placeholder="Search pages or devices" aria-label="Search pages or devices" style="width:100%"><div id="palette-results"></div>',el=>{const links=$$('.nav-link').map(a=>({name:a.title,url:a.getAttribute('href')})).concat(D.state.devices.map(d=>({name:d.name+' · '+d.ip,url:'health.html?device='+d.id})));const draw=()=>{$('#palette-results',el).innerHTML=links.filter(l=>l.name.toLowerCase().includes($('#palette-query',el).value.toLowerCase())).map(l=>`<a class="palette-link" href="${h(l.url)}">${icon('arrow')}${h(l.name)}</a>`).join('')||empty();};$('#palette-query',el).oninput=draw;draw();$('#palette-query',el).focus();});
  if($('#command-search'))$('#command-search').onclick=palette;
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();palette();}});
  if($('#help-button'))$('#help-button').onclick=()=>modal('Narsika','<p class="notice">Add a credential profile and a device in Inventory, verify its SSH host fingerprint, then open Device health.</p><p class="muted">Monitoring runs while its page is open. Missing or unsupported measurements appear as N/A. Configuration operations run on the server and remain visible in run history.</p>');
  let heartbeat=null,heartbeatController=null;
  async function checkServer(){
    clearTimeout(heartbeat);if(document.hidden)return;
    heartbeatController=new AbortController();const controller=heartbeatController;const timeout=setTimeout(()=>controller.abort(),5000);
    let connected=false;try{const response=await fetch('/healthz',{cache:'no-store',signal:controller.signal});connected=response.ok&&(await response.json()).status==='ok';}catch{}
    clearTimeout(timeout);
    const label=$('#server-status');if(label){label.textContent=connected?'Connected':'Unavailable';label.className='badge '+(connected?'online':'offline');}
    if($('#server-connection'))$('#server-connection').textContent=connected?'Server connected':'Connection unavailable';
    heartbeat=setTimeout(checkServer,15000);
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(heartbeat);heartbeatController?.abort();}else checkServer();});
  window.addEventListener('pagehide',()=>{clearTimeout(heartbeat);heartbeatController?.abort();});
  if($('#server-status'))checkServer();
  window.addEventListener('narsika:change',refresh);icons();refresh();
})();
