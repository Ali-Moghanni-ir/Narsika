(() => {
  const initial=JSON.parse(document.getElementById('bootstrap')?.textContent||'{}');
  // The vendor name comes from the server's platform registry, never from a hard-coded list.
  const normalize=data=>({...data,platforms:data.platforms||[],devices:(data.devices||[]).map(d=>({...d,ip:d.ip_address,vendor:(data.platforms||[]).find(p=>p.id===d.platform)?.vendor||d.platform,port:d.ssh_port,status:d.health?.status||'unknown'})),groups:data.groups||[],credentials:data.credentials||[],users:data.users||[],audit:data.audit||[],settings:{...data.settings,role:data.user?.role||'VIEWER'}});
  let reloadGeneration=0,latestReload=null;
  const D={state:normalize(initial),reload(){const generation=++reloadGeneration,view=document.body.dataset.page;
    // A refresh after a write must not reuse an older in-flight read.
    const request=window.Narsika.apiFetch('/api/bootstrap?view='+encodeURIComponent(view)).then(result=>{if(generation!==reloadGeneration)return latestReload;D.state=normalize(result.data);window.dispatchEvent(new CustomEvent('narsika:change'));return D.state;},error=>{if(generation!==reloadGeneration)return latestReload;throw error;});latestReload=request;return request;}};
  window.NarsikaData=D;
})();
