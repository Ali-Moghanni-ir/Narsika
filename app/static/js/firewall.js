/* Intent-only client. Device commands are compiled and validated on the server. */
(() => {
  'use strict';
  const N=window.Narsika, {$,$$,h,icon,apiFetch,download}=N;
  const bootstrap=JSON.parse($('#bootstrap').textContent);
  const devices=bootstrap.devices, role=bootstrap.user.role;
  const state={device:null, snapshot:null, changes:[], capabilities:null, busy:false, applying:false, receipt:null, poll:null, timer:null, watchId:null};
  const dialog=$('#fw-dialog');
  const api=async(path,body)=> (await apiFetch('/api/firewall'+path,body===undefined?{}:{method:'POST',body})).data;
  const label=r=>r.service==='custom'?`${r.protocol.toUpperCase()}${r.port?' / '+r.port:''}`:r.service.toUpperCase();
  const feedback=(message,type='')=>{const el=$('#fw-feedback');el.hidden=!message;el.className='fw-feedback '+type;el.textContent=message;};
  function controls(){
    $('#fw-refresh').disabled=!state.device||state.busy;
    $('#fw-device').disabled=state.busy;
    $('#fw-review').disabled=!state.device||!state.changes.length||state.busy;
    $('#fw-review').textContent=state.busy?'Working…':role==='VIEWER'?'Preview change set':'Review change set';
    $('#fw-custom').disabled=!state.device||state.busy||role==='OPERATOR';
    $$('.fw-service-actions button').forEach(b=>b.disabled=!state.device||state.busy);
    const phase=state.receipt?.run&& !['PENDING','RUNNING'].includes(state.receipt.run.status)?3:state.receipt?2:state.changes.length?1:0;
    $$('.fw-steps > span').forEach((step,index)=>{step.classList.toggle('active',index===phase);if(index===phase)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');});
  }
  async function busy(fn){
    if(state.busy)return;
    state.busy=true;controls();feedback('');
    try{await fn();}catch(ex){feedback(ex.message,'error');}
    finally{state.busy=false;controls();}
  }
  function scope(){
    const common={source:$('#fw-source').value.trim(),destination:$('#fw-destination').value.trim()};
    return state.device.platform==='mikrotik'?{...common,chain:$('#fw-chain').value,position:$('#fw-position').value}:
      {...common,acl:$('#fw-acl').value,sequence:$('#fw-sequence').value};
  }
  function renderServices(){
    const presets=(state.capabilities?.presets||[]).filter(p=>p.id!=='winbox'||state.device?.platform!=='cisco').map(p=>p.id==='ssh'&&state.device?{...p,port:state.device.ssh_port}:p);
    $('#fw-services').innerHTML=presets.map(p=>`<article class="fw-service"><div class="fw-service-top">${icon(p.icon)}<span class="fw-port">${h(p.protocol.toUpperCase())}${p.port?' / '+p.port:''}</span></div><h3>${h(p.name)}</h3><p>${h(p.note)}</p><div class="fw-service-actions"><button data-service="${h(p.id)}" data-action="allow" aria-label="Stage Allow ${h(p.name)}">Allow</button><button data-service="${h(p.id)}" data-action="block" aria-label="Stage Block ${h(p.name)}">Block</button></div></article>`).join('');
    $$('.fw-service-actions button').forEach(b=>b.onclick=()=>{
      const preset=presets.find(p=>p.id===b.dataset.service);
      stage({...scope(),service:preset.id,protocol:preset.protocol,port:preset.port,action:b.dataset.action});
    });controls();
  }
  function stage(rule,index=null){
    if(state.busy||!state.device)return false;
    if(state.device.platform==='cisco'&&(!rule.acl||state.snapshot?.unsupported_acls?.includes(rule.acl))){feedback('Read the firewall and select an editable named extended ACL first.','error');return false;}
    if(index===null&&state.changes.length>=20){feedback('A change set can contain at most 20 rules.','error');return false;}
    if(index===null)state.changes.push(rule);else state.changes[index]=rule;
    if(state.device.platform==='cisco'&&index===null)$('#fw-sequence').value=Number(rule.sequence)+10;
    state.receipt=null;renderCart();feedback('Change staged locally. No commands have been sent.');
    return true;
  }
  function renderCart(){
    $('#fw-cart-count').textContent=state.changes.length;
    $('#fw-clear').disabled=!state.changes.length||state.busy;
    $('#fw-cart-items').innerHTML=state.changes.length?state.changes.map((r,i)=>`<div class="fw-cart-item"><div class="fw-cart-item-head"><span class="fw-muted">${i+1}</span><strong>${h(label(r))}</strong><span class="fw-intent ${h(r.action)}">${h(r.action.toUpperCase())}</span></div><p>${h(r.source)} → ${h(r.destination)}<br>${h(r.chain||r.acl)} · ${h(r.position||'sequence '+r.sequence)}${r.port?' · port '+h(r.port):''}</p><div class="fw-item-tools"><button class="btn" data-edit="${i}" aria-label="Edit change ${i+1}">Edit</button><button class="btn" data-up="${i}" ${i===0?'disabled':''} aria-label="Move change ${i+1} up">↑</button><button class="btn" data-down="${i}" ${i===state.changes.length-1?'disabled':''} aria-label="Move change ${i+1} down">↓</button><button class="btn ghost fw-remove" data-remove="${i}" aria-label="Remove staged change ${i+1}">Remove</button></div></div>`).join(''):
      `<div class="fw-cart-empty"><div class="fw-cart-orbit">${icon('layers')}</div><h3>Build your next change</h3><p>Choose a service or add a custom rule. Review everything together before it touches the network.</p></div>`;
    $$('[data-remove]').forEach(b=>b.onclick=()=>{if(state.busy)return;state.changes.splice(+b.dataset.remove,1);state.receipt=null;renderCart();});
    $$('[data-edit]').forEach(b=>b.onclick=()=>editRule(+b.dataset.edit));
    for(const direction of ['up','down'])$$('[data-'+direction+']').forEach(b=>b.onclick=()=>{
      if(state.busy)return;const i=+b.dataset[direction],j=i+(direction==='up'?-1:1);
      [state.changes[i],state.changes[j]]=[state.changes[j],state.changes[i]];state.receipt=null;renderCart();
    });controls();
  }
  function openModal(title,body,footer=''){
    if(state.applying)return;
    clearInterval(state.timer);
    $('#fw-dialog-content').innerHTML=`<div class="fw-modal-head"><div><div class="eyebrow">NARSIKA / FIREWALL CONTROL</div><h2 id="fw-dialog-title">${h(title)}</h2></div><button class="btn ghost icon-only" id="fw-close-modal" aria-label="Close dialog">${icon('close')}</button></div><div class="fw-modal-body">${body}<div id="fw-modal-error" class="fw-dialog-error" role="alert"></div></div>${footer}`;
    $('#fw-close-modal').onclick=()=>{if(!state.applying)dialog.close();};
    if(!dialog.open)dialog.showModal();
  }
  dialog.addEventListener('close',()=>clearInterval(state.timer));
  dialog.addEventListener('cancel',event=>{if(state.applying)event.preventDefault();});
  function editRule(index=null){
    if(!state.device||state.busy)return;
    const rule=index===null?{...scope(),service:'custom',protocol:'tcp',port:8080,action:'allow'}:{...state.changes[index]};
    const mt=state.device.platform==='mikrotik';
    const options=(values,current)=>values.map(v=>`<option value="${h(v)}" ${String(current)===String(v)?'selected':''}>${h(v)}</option>`).join('');
    openModal(index===null?'Custom rule':'Edit staged rule',`<form id="fw-rule-form"><div class="fw-modal-fields"><label>Action<select name="action">${options(['allow','block'],rule.action)}</select></label><label>Protocol<select name="protocol" ${rule.service!=='custom'?'disabled':''}>${options(['tcp','udp','icmp','ip'],rule.protocol)}</select></label><label>Destination port<input name="port" type="number" min="1" max="65535" value="${h(rule.port||'')}"></label><label>Source network<input name="source" value="${h(rule.source)}" required maxlength="32"></label><label>Destination network<input name="destination" value="${h(rule.destination)}" required maxlength="32"></label>${mt?`<label>Chain<select name="chain">${options(['input','forward','output'],rule.chain)}</select></label><label>Position<select name="position">${options(['first','last'],rule.position)}</select></label>`:`<label>ACL<select name="acl">${options(Object.keys(state.snapshot?.acls||{}),rule.acl)}</select></label><label>Sequence<input name="sequence" type="number" min="1" max="2147483646" value="${h(rule.sequence)}" required></label>`}</div><p class="fw-caption">Use IPv4/CIDR or “any”. Ping matches echo requests only. Any IP includes all IPv4 protocols. Custom rules and high-risk changes require an administrator to apply.</p><button class="btn primary" type="submit">${index===null?'Add to change set':'Save staged change'}</button></form>`);
    const form=$('#fw-rule-form');
    const updatePort=()=>{form.elements.port.disabled=!['tcp','udp'].includes(form.elements.protocol.value);form.elements.port.required=!form.elements.port.disabled;};
    form.elements.protocol.onchange=updatePort;updatePort();
    form.onsubmit=e=>{e.preventDefault();const data=Object.fromEntries(new FormData(form));const next={...rule,...data,protocol:form.elements.protocol.value};if(!['tcp','udp'].includes(next.protocol))next.port=null;if(stage(next,index))dialog.close();else $('#fw-modal-error').textContent=$('#fw-feedback').textContent;};
  }
  function renderRules(){
    const snapshot=state.snapshot;
    if(!snapshot){$('#fw-rules').innerHTML=`<div class="fw-empty">${icon('shield')}<h3>Start with the current state</h3><p>Select a device and read its firewall over SSH.<br>No sample rules or assumed statuses.</p></div>`;$('#fw-rule-count').textContent='—';$('#fw-snapshot-label').textContent='Not read yet';$('#fw-bindings').innerHTML='';return;}
    $('#fw-rule-count').textContent=snapshot.rules.length;
    $('#fw-snapshot-label').textContent='Read '+new Date(snapshot.captured_at).toLocaleTimeString();
    $('#fw-bindings').innerHTML=snapshot.bindings.length?`<div class="fw-binding">Observed attachment points<br>${snapshot.bindings.map(h).join('<br>')}</div>`:'';
    const query=$('#fw-search').value.toLowerCase(),rows=snapshot.rules.filter(r=>JSON.stringify(r).toLowerCase().includes(query));
    if(!rows.length){$('#fw-rules').innerHTML=`<div class="fw-empty"><h3>${query?'No matching rules':'No supported rules found'}</h3><p>${query?'Try a different search.':state.device.platform==='cisco'?'This adapter needs an existing named extended IPv4 ACL. Standard and numbered ACLs are not editable here.':'No IPv4 filter entries were returned by the device.'}</p></div>`;return;}
    const mt=state.device.platform==='mikrotik';
    const trace=r=>mt&&/^NARSIKA_FW_[a-f0-9]{16}$/.test(r.comment||'')?'Device tag':r.receipt_id?'Receipt match':'Unattributed';
    $('#fw-rules').innerHTML=`<table class="fw-table"><thead><tr><th>${mt?'Order':'Sequence'}</th><th>${mt?'Chain / state':'ACL'}</th><th>Observed configuration</th><th>Traceability</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="mono">${mt?r.index:r.sequence}</td><td>${h(mt?r.chain:r.acl)}${mt?`<br><span class="fw-muted">${r.disabled?'Disabled':'Enabled'}</span>`:''}</td><td class="fw-rule-code">${h(mt?r.raw.replace('/ip firewall filter add ',''):r.body)}</td><td><span class="badge" title="${h(r.receipt_id?'Matches receipt '+r.receipt_id+'; not proof of exclusive ownership.':'Device configuration observed over SSH.')}">${trace(r)}</span></td></tr>`).join('')}</tbody></table>`;
  }
  function setSnapshot(snapshot){
    state.snapshot=snapshot;renderRules();
    if(!$('#fw-management-source').value)$('#fw-management-source').value=snapshot.source_ip_hint||'';
    const selected=$('#fw-acl').value;
    const editable=Object.keys(snapshot.acls).filter(name=>!snapshot.unsupported_acls?.includes(name));
    $('#fw-acl').innerHTML='<option value="">Select existing ACL</option>'+Object.keys(snapshot.acls).map(name=>`<option value="${h(name)}" ${editable.includes(name)?'':'disabled'}>${h(name)}${editable.includes(name)?'':' (read-only)'}</option>`).join('');
    if(editable.includes(selected))$('#fw-acl').value=selected;
    else if(editable.length===1)$('#fw-acl').value=editable[0];
  }
  async function readFirewall(){await busy(async()=>{const data=await api(`/devices/${state.device.id}/refresh`,{});setSnapshot(data.state);feedback('Firewall read from the device. Review will read it again before preparing your plan.','success');});}
  async function history(){
    if(!state.device)return;
    const target=state.device.id;
    try{
      const data=await api('/history?device='+target);if(state.device?.id!==target)return;
      $('#fw-history').className='fw-table-wrap';
      $('#fw-history').innerHTML=data.items.length?`<table class="fw-table"><thead><tr><th>Created</th><th>Changes</th><th>Risk</th><th>Status</th><th>Receipt</th></tr></thead><tbody>${data.items.map(r=>`<tr><td>${h(new Date(r.created_at).toLocaleString())}</td><td>${r.count}</td><td>${h(r.risk)}</td><td>${h(r.status)}</td><td><button class="btn fw-history-open" data-receipt="${h(r.id)}">View</button></td></tr>`).join('')}</tbody></table>`:'<div class="fw-empty">No firewall reviews for this device yet.</div>';
      $$('[data-receipt]').forEach(b=>b.onclick=()=>busy(async()=>{const receipt=await api('/reviews/'+b.dataset.receipt);showReview(receipt,false);if(receipt.run){renderRun(receipt);startWatch(receipt.id);}}));
    }catch(ex){if(state.device?.id===target)$('#fw-history').textContent=ex.message;}
  }
  function showReview(receipt,canApply=true){
    const p=receipt.plan,lockout=p.risk==='LOCKOUT',run=receipt.run;
    const readOnly=!canApply||role==='VIEWER'||receipt.run_id||receipt.status!=='REVIEWED';
    const commands=p.items.map(i=>i.parents.concat(i.commands).join('\n')).join('\n\n');
    const recover=`Narsika firewall recovery — review ${receipt.id}\nDevice: ${p.device.name} (${p.device.ip_address})\nChecksum: ${receipt.checksum}\n\nMANUAL RECOVERY ONLY. Check the current device configuration before running.\nCisco: enter enable/configure terminal first; these changes are running-config only.\nAutomatic timed rollback is NOT armed. Use console/OOB if SSH is unavailable.\n\n${p.recovery.join('\n')}\n`;
    const ack=(name,message)=>`<label class="fw-ack"><input type="checkbox" name="${name}"><span>${h(message)}</span></label>`;
    openModal(readOnly?'Review receipt':'Review before applying',`<div class="fw-review-summary"><div><span>TARGET DEVICE</span><strong>${h(p.device.name)}</strong><br>${h(p.device.ip_address)} · ${h(p.device.platform)}</div><div><span>SOURCE AS SEEN BY TARGET</span><strong>${h(p.source_ip)}</strong></div><div><span>CHANGE SET</span><strong>${p.items.length} changes · ${h(p.risk)}</strong></div></div><div class="fw-risk ${lockout?'lockout':''}"><strong>${lockout?'Management access may be lost':p.risk==='HIGH'?'High-impact changes need your attention':'Review scope and rule order'}</strong>${lockout?'You are allowed to proceed as administrator. This can interrupt Narsika access, monitoring and later operations. Recovery may require console or an independent management path.':'Rules are evaluated in order. A rule can be shadowed by an earlier rule or affect traffic beyond this workflow.'}</div><table class="fw-table"><thead><tr><th>Intent</th><th>Traffic match</th><th>Placement</th><th>Risk</th></tr></thead><tbody>${p.items.map(i=>`<tr><td>${h(i.label)}</td><td>${h(i.rule.source)} → ${h(i.rule.destination)}<br>${h(i.rule.protocol.toUpperCase())}${i.rule.port?' / '+i.rule.port:''}</td><td>${h(i.rule.chain||i.rule.acl)} / ${h(i.rule.position||i.rule.sequence)}</td><td>${h(i.risk)}</td></tr>`).join('')}</tbody></table><details class="fw-review-detail" open><summary>Resulting placement</summary><ol>${p.final_order.map(v=>`<li>${h(v)}</li>`).join('')}</ol><p class="fw-caption">Existing rules stay in place. This is placement, not a packet-flow simulation.</p>${p.baseline.bindings.length?`<pre>${h(p.baseline.bindings.join('\n'))}</pre>`:''}</details><ul class="fw-review-warnings">${p.warnings.map(w=>`<li>${h(w)}</li>`).join('')}</ul><details class="fw-review-detail"><summary>Inspect generated commands</summary><pre>${h(commands)}</pre></details><details class="fw-review-detail"><summary>Manual recovery commands</summary><pre>${h(p.recovery.join('\n'))}</pre><p class="fw-caption">These commands are not automatically scheduled. Verify ownership and sequence before manual execution.</p></details><button class="btn" id="fw-download-recovery">${icon('download')} Download review & recovery</button>${readOnly?`<p class="fw-caption">Receipt ${h(receipt.id)} · ${h(receipt.status)}${role==='VIEWER'?' · Viewer access: execution disabled.':''}</p>`:`<form id="fw-ack-form" class="fw-check-section">${ack('target_confirmed','I checked the target device, traffic scope, placement and all affected ACL bindings.')}${ack('source_confirmed','I confirmed Narsika’s source address as seen by this target, including any NAT.')}${ack('risk_ack','I reviewed the warnings and understand the effects are not guaranteed by this preview.')}${ack('recovery_saved','I downloaded the recovery instructions and can access them outside Narsika.')}${ack('no_rollback_ack','I understand that automatic timed rollback is NOT available.')} ${lockout?`<div class="fw-confirm-fields">${ack('oob_confirmed','I have working console or independent out-of-band access for recovery.')}<label>Type the device name: ${h(p.device.name)}<input name="device_name" autocomplete="off" spellcheck="false"></label><label>Type DISCONNECT to accept possible loss of management<input name="phrase" autocomplete="off" spellcheck="false" placeholder="DISCONNECT"></label></div>`:''}</form>`}`,
      `<div class="fw-modal-footer"><span id="fw-expiry">${readOnly?'Immutable review · '+h(receipt.checksum.slice(0,12)):'Review expires in 10 minutes'}</span><div><button class="btn" id="fw-back">${readOnly?'Close':'Back to changes'}</button>${readOnly?'':`<button class="btn primary ${lockout?'fw-danger-button':''}" id="fw-apply" disabled>${lockout?'Accept risk & apply':'Apply '+p.items.length+' changes'}</button>`}</div></div>`);
    $('#fw-back').onclick=()=>{if(!state.applying)dialog.close();};
    let saved=false,applying=false;
    $('#fw-download-recovery').onclick=()=>{download('narsika-firewall-'+receipt.id+'.txt',recover+'\nREVIEWED COMMANDS\n'+commands+'\n\nWARNINGS\n'+p.warnings.join('\n'));saved=true;update();};
    function update(){
      if(readOnly)return;
      const form=$('#fw-ack-form');if(!form)return;
      const remaining=Math.max(0,Math.floor(receipt.expires_at-Date.now()/1000));
      $('#fw-expiry').textContent=remaining?`Valid for ${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')} · ${receipt.checksum.slice(0,8)}`:'Review expired — close and review again';
      const accepted=$$('input[type=checkbox]',form).every(el=>el.checked);
      const typed=!lockout||(form.elements.device_name.value===p.device.name&&form.elements.phrase.value==='DISCONNECT');
      $('#fw-apply').disabled=applying||!saved||!accepted||!typed||!remaining;
    }
    if(!readOnly){
      $('#fw-ack-form').oninput=update;state.timer=setInterval(update,1000);update();
      $('#fw-apply').onclick=async()=>{
        if(applying)return;applying=true;state.applying=true;state.busy=true;controls();update();
        $('#fw-close-modal').disabled=true;$('#fw-back').disabled=true;
        const form=$('#fw-ack-form'),body={checksum:receipt.checksum};
        $$('input',form).forEach(el=>body[el.name]=el.type==='checkbox'?el.checked:el.value);
        try{
          const data=await api('/reviews/'+receipt.id+'/apply',body);
          dialog.close();state.changes=[];renderCart();state.receipt={...receipt,run_id:data.run.id,run:data.run};
          renderRun(state.receipt);startWatch(receipt.id);history();feedback('Change set queued. Watch the verified result below.','success');
          $('#fw-run-panel').scrollIntoView({behavior:'smooth',block:'center'});
        }catch(ex){$('#fw-modal-error').textContent=ex.message+' If the response was lost, inspect review history before retrying.';applying=false;update();}
        finally{state.applying=false;state.busy=false;controls();$('#fw-close-modal').disabled=false;$('#fw-back').disabled=false;}
      };
    }
  }
  const terminalStatuses={SUCCESS:'The requested configuration was verified through a fresh SSH connection. End-to-end traffic is not tested.',PARTIAL:'Only part of the requested configuration was verified. Inspect the device before attempting another change.',APPLIED_UNVERIFIED:'Execution was attempted but the result could not be verified. Access may be lost. Use console/OOB and the downloaded recovery instructions.',FAILED:'The operation failed. Read the output to see whether execution started.',CANCELLED:'Cancellation completed. Commands already sent are not undone.',INTERRUPTED:'The worker restarted. The device may contain partial changes. Inspect it before retrying.'};
  function renderRun(receipt){
    const run=receipt.run;if(!run)return;state.receipt=receipt;controls();
    $('#fw-run-panel').hidden=false;$('#fw-run-title').textContent=`${receipt.plan.device.name} · run #${run.id}`;$('#fw-run-status').textContent=receipt.status||run.status;
    $('#fw-run-message').textContent=terminalStatuses[run.status]||'Preflight → encrypted backup → execution → fresh SSH verification. Keep your recovery instructions available.';
    $('#fw-run-output').textContent=run.output||'Waiting for the operation worker…';
    $('#fw-cancel-run').disabled=!['PENDING','RUNNING'].includes(run.status)||run.cancel_requested||role==='VIEWER';
    $('#fw-open-receipt').onclick=()=>showReview(receipt,false);
  }
  function startWatch(id){state.watchId=id;watch(id);}
  async function watch(id){
    if(state.watchId!==id)return;
    clearTimeout(state.poll);
    if(document.hidden)return;
    try{
      const receipt=await api('/reviews/'+id);
      if(state.watchId!==id||receipt.plan.device.id!==state.device?.id)return;
      renderRun(receipt);
      if(['PENDING','RUNNING'].includes(receipt.run?.status))state.poll=setTimeout(()=>watch(id),1800);
      else{history();feedback(terminalStatuses[receipt.run?.status]||'Review updated.',receipt.run?.status==='SUCCESS'?'success':'');}
    }catch(ex){if(state.watchId!==id)return;feedback('Status unavailable: '+ex.message+'. Do not assume the operation stopped.','error');state.poll=setTimeout(()=>watch(id),6000);}
  }
  $('#fw-cancel-run').onclick=()=>N.confirm('Request cancellation','Cancellation does not undo commands already sent. A fresh device check is still required.',async()=>{
    if(!state.receipt?.run)return;
    await apiFetch('/api/automation/runs/'+state.receipt.run.id+'/cancel',{method:'POST',body:{}});startWatch(state.receipt.id);
  });
  $('#fw-device').innerHTML='<option value="">Select a device from inventory</option>'+devices.map(d=>`<option value="${d.id}">${h(d.name)} · ${h(d.ip_address)}</option>`).join('');
  $('#fw-device').onchange=()=>{
    const selected=$('#fw-device').value;
    if(state.changes.length&&!window.confirm('Switch target and discard the staged changes?')){$('#fw-device').value=state.device?.id||'';return;}
    clearTimeout(state.poll);state.watchId=null;state.device=devices.find(d=>String(d.id)===selected)||null;state.snapshot=null;state.changes=[];state.receipt=null;
    $('#fw-run-panel').hidden=true;$('#fw-management-source').value='';$('#fw-search').value='';feedback('');
    $('#fw-target-meta').innerHTML=state.device?`<strong>${h(state.device.platform==='cisco'?'Cisco IOS':'MikroTik RouterOS')}</strong>${h(state.device.ip_address)} · SSH ${state.device.ssh_port} · IPv4 filter`:'Your inventory. Your real configuration.';
    const mt=state.device?.platform!=='cisco';$('#fw-chain-field').hidden=!mt;$('#fw-position-field').hidden=!mt;$('#fw-acl-field').hidden=mt;$('#fw-sequence-field').hidden=mt;
    $('#fw-scope-note').textContent=mt?'First inserts ahead of existing rules; Last may be shadowed. Review shows the resulting placement.':'Only existing named extended ACLs with a free sequence. ACLs containing remarks are read-only because IOS may hide their sequences. Bindings remain unchanged.';
    renderRules();renderServices();renderCart();if(state.device)history();else $('#fw-history').innerHTML='<div class="fw-empty">Choose a target to view its history.</div>';
  };
  $('#fw-refresh').onclick=readFirewall;$('#fw-search').oninput=renderRules;$('#fw-custom').onclick=()=>editRule();
  $('#fw-clear').onclick=()=>{if(state.busy)return;N.confirm('Clear staged changes','Only the local change set will be cleared. Device configuration is not affected.',()=>{state.changes=[];state.receipt=null;renderCart();});};
  $('#fw-review').onclick=()=>busy(async()=>{
    const receipt=await api(`/devices/${state.device.id}/reviews`,{source_ip:$('#fw-management-source').value.trim(),changes:state.changes});
    setSnapshot(receipt.plan.baseline);state.receipt=receipt;showReview(receipt);history();
  });
  for(const name of ['rules','history']){
    const tab=$('#fw-'+name+'-tab');
    tab.onclick=()=>{for(const n of ['rules','history']){const active=n===name;$('#fw-'+n+'-tab').setAttribute('aria-selected',String(active));$('#fw-'+n+'-tab').tabIndex=active?0:-1;$('#fw-'+n+'-panel').hidden=!active;}if(name==='history')history();};
    tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=$('#fw-'+(name==='rules'?'history':'rules')+'-tab');next.click();next.focus();}};
  }
  $('#fw-role-note').textContent=role==='VIEWER'?'Viewer · inspect and preview only. Execution is disabled.':role==='OPERATOR'?'Operator · scoped preset changes only. High-risk and custom rules require Admin.':'Admin · high-risk changes require explicit acknowledgements.';
  window.addEventListener('beforeunload',e=>{if(state.changes.length){e.preventDefault();e.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(state.poll);else if(state.watchId)watch(state.watchId);});
  window.addEventListener('pagehide',()=>{clearTimeout(state.poll);clearInterval(state.timer);});
  api('/capabilities').then(c=>{state.capabilities=c;renderServices();}).catch(e=>feedback(e.message,'error'));
  renderCart();controls();
})();
