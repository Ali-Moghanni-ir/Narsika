(() => {
  const initial=JSON.parse(document.getElementById('bootstrap')?.textContent||'{}');
  const normalize=data=>({...data,devices:(data.devices||[]).map(d=>({...d,ip:d.ip_address,vendor:d.platform==='cisco'?'Cisco':'MikroTik',port:d.ssh_port,status:d.health?.status||'unknown'})),groups:data.groups||[],credentials:data.credentials||[],users:data.users||[],audit:data.audit||[],settings:{...data.settings,role:data.user?.role||'VIEWER'}});
  const D={state:normalize(initial),async reload(){const result=await window.Narsika.apiFetch('/api/bootstrap');D.state=normalize(result.data);window.dispatchEvent(new CustomEvent('narsika:change'));return D.state;}};
  window.NarsikaData=D;
})();
