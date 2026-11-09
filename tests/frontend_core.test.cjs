/* Behavioral checks for shared UI code. This is not a browser renderer. */
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function element(tag='DIV') {
  return {tagName:tag,dataset:{},style:{},disabled:false,title:'',textContent:'',children:[],listeners:{},
    setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
    addEventListener(name,fn){this.listeners[name]=fn;},
    prepend(child){this.children.unshift(child);},append(child){this.children.push(child);},remove(){},
    querySelector(selector){return selector==='[data-error]'?this.children.find(c=>'error' in c.dataset)||null:null;},
    querySelectorAll(){return [];},classList:{toggle(){},add(){}}};
}
function runtime(fetch=async()=>({ok:true,status:200,json:async()=>({data:{}})})) {
  const dialog=element(),notices=element(),permissions=[];
  Object.defineProperty(dialog,'innerHTML',{set(value){this.markup=value;this.nodes={'[data-close]':element('BUTTON'),'#cancel-confirm':element('BUTTON'),'#accept-confirm':element('BUTTON'),'.dialog-body':element()};},get(){return this.markup;}});
  dialog.querySelector=selector=>dialog.nodes?.[selector]||null;
  dialog.querySelectorAll=selector=>selector==='button'?Object.values(dialog.nodes).filter(e=>e.tagName==='BUTTON'):[];
  dialog.showModal=()=>dialog.open=true;dialog.close=()=>dialog.open=false;
  const document={body:{dataset:{page:'inventory'},classList:{toggle(){}}},hidden:false,
    querySelector(selector){return selector==='#dialog'?dialog:selector==='#toasts'?notices:selector==='meta[name=csrf-token]'?{content:'fixture-token'}:null;},
    querySelectorAll(selector){return selector==='[data-permission]'?permissions:[];},createElement:element,addEventListener(){}};
  const state={user:{id:1,name:'Test operator',role:'OPERATOR'},devices:[],settings:{role:'OPERATOR',workspace:'Test'}};
  const window={NarsikaData:{state},addEventListener(){}};
  const context=vm.createContext({document,window,location:{href:'http://localhost/index.html'},fetch,FormData,Blob,URL,AbortController,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync('app/static/js/core.js','utf8'),context);
  return {N:window.Narsika,dialog,permissions,state,notices};
}

test('confirmation submits once and stays open while pending',async()=>{
  const {N,dialog}=runtime();let resolve,calls=0;
  N.confirm('Run operation','Check target',()=>{calls++;return new Promise(r=>resolve=r);});
  const accept=dialog.querySelector('#accept-confirm'),first=accept.onclick();
  await accept.onclick();assert.equal(calls,1);assert.equal(dialog.open,true);assert.equal(accept.disabled,true);
  let prevented=false;dialog.listeners.cancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);
  resolve();await first;assert.equal(dialog.open,false);assert.equal(accept.disabled,false);
});

test('confirmation shows a failure in the dialog and allows a deliberate retry',async()=>{
  const {N,dialog}=runtime();let calls=0;
  N.confirm('Run operation','Check target',async()=>{if(++calls===1)throw new Error('Controlled rejection');});
  const accept=dialog.querySelector('#accept-confirm');await accept.onclick();
  assert.equal(dialog.open,true);assert.equal(dialog.querySelector('.dialog-body').children[0].textContent,'Controlled rejection');
  assert.equal(accept.disabled,false);await accept.onclick();assert.equal(calls,2);assert.equal(dialog.open,false);
});

test('permissions preserve runtime disabled state and original action labels',()=>{
  const {N,permissions,state}=runtime();const button=element('BUTTON');button.dataset.permission='operate';button.disabled=true;button.title='Scan already running';permissions.push(button);
  N.permissions();assert.equal(button.disabled,true);assert.equal(button.title,'Scan already running');
  state.settings.role='VIEWER';N.permissions();assert.equal(button.disabled,true);
  state.settings.role='ADMIN';N.permissions();assert.equal(button.disabled,true);assert.equal(button.title,'Scan already running');
  const link=element('A');link.dataset.permission='operate';permissions.push(link);state.settings.role='VIEWER';N.permissions();
  let prevented=false;link.onclick({preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(link.tabIndex,-1);
});

test('mutation timeout never retries or implies that the operation stopped',async()=>{
  let calls=0;const {N}=runtime((path,options)=>{calls++;return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{const error=new Error();error.name='AbortError';reject(error);},{once:true}));});
  await assert.rejects(N.apiFetch('/api/automation/runs',{method:'POST',body:{},timeout:10}),error=>error.code==='REQUEST_TIMEOUT'&&/may have been accepted/.test(error.message));
  assert.equal(calls,1);
});

test('invalid JSON shape and server error identity remain distinguishable',async()=>{
  const malformed=runtime(async()=>({ok:true,status:200,json:async()=>null}));
  await assert.rejects(malformed.N.apiFetch('/api/test'),/invalid response/);
  const denied=runtime(async()=>({ok:false,status:409,json:async()=>({error:{code:'BUSY',message:'Target busy',request_id:'test-request'}})}));
  await assert.rejects(denied.N.apiFetch('/api/test'),error=>error.code==='BUSY'&&error.status===409&&error.request_id==='test-request');
});

test('run badges retain precise state and escape unrecognized labels',()=>{
  const {N}=runtime();
  for(const status of ['PENDING','PARTIAL','APPLIED_UNVERIFIED','CANCELLED','INTERRUPTED']){
    assert.ok(N.badge(status).includes('class="badge '+status.toLowerCase()+'"'));
    assert.ok(N.badge(status).includes('>'+status+'</span>'));
  }
  assert.ok(!N.badge('<script>').includes('<script>'));
});

test('a post-write reload does not reuse or lose to an older inventory read',async()=>{
  const requests=[],events=[];
  const document={body:{dataset:{page:'inventory'}},getElementById(){return {textContent:'{}'};}};
  const window={Narsika:{apiFetch(path){return new Promise((resolve,reject)=>requests.push({path,resolve,reject}));}},dispatchEvent(event){events.push(event);}};
  const context=vm.createContext({window,document,CustomEvent:class {constructor(type){this.type=type;}}});
  vm.runInContext(fs.readFileSync('app/static/js/data.js','utf8'),context);
  const D=window.NarsikaData,old=D.reload(),fresh=D.reload();
  assert.equal(requests.length,2);assert.equal(requests[1].path,'/api/bootstrap?view=inventory');
  requests[1].resolve({data:{devices:[{id:2,name:'New saved device'}]}});await fresh;
  requests[0].resolve({data:{devices:[{id:1,name:'Stale device'}]}});await old;
  assert.equal(D.state.devices[0].id,2);assert.equal(events.length,1);
});

const REGISTRY=[{id:'cisco',label:'Cisco IOS / IOS XE',vendor:'Cisco',capabilities:['backup','firewall','health','interfaces','playbooks','vlan']},
  {id:'mikrotik',label:'MikroTik RouterOS',vendor:'MikroTik',capabilities:['backup','firewall','health','interfaces','playbooks']},
  {id:'linux',label:'Linux server',vendor:'Linux',capabilities:['backup','health','interfaces','playbooks']}];

test('device vendors come from the server platform registry',()=>{
  const bootstrap={platforms:REGISTRY,devices:[{id:1,platform:'linux',ip_address:'10.0.0.9',ssh_port:22},{id:2,platform:'mikrotik',ip_address:'10.0.0.1',ssh_port:22},{id:3,platform:'future-os',ip_address:'10.0.0.2',ssh_port:22}]};
  const document={body:{dataset:{page:'inventory'}},getElementById(){return {textContent:JSON.stringify(bootstrap)};}};
  const window={};
  vm.runInContext(fs.readFileSync('app/static/js/data.js','utf8'),vm.createContext({window,document,CustomEvent:class {}}));
  // Spread copies the array out of the VM realm so deepEqual compares values only.
  const vendors=[...window.NarsikaData.state.devices.map(d=>d.vendor)];
  // An unknown platform keeps its own ID instead of being shown as another vendor.
  assert.deepEqual(vendors,['Linux','MikroTik','future-os']);
  assert.equal(window.NarsikaData.state.platforms.length,3);
});

test('platform helpers follow registry capabilities',()=>{
  const {N,state}=runtime();
  state.platforms=REGISTRY;
  state.devices=[{id:1,name:'core-sw',ip:'10.0.0.1',platform:'cisco',vendor:'Cisco'},{id:2,name:'web-01',ip:'10.0.0.9',platform:'linux',vendor:'Linux'}];
  assert.equal(N.supports(state.devices[1],'vlan'),false);
  assert.equal(N.supports(state.devices[1],'health'),true);
  assert.equal(N.supports({platform:'unknown'},'health'),false);
  assert.ok(N.options(null,'vlan').includes('core-sw')&&!N.options(null,'vlan').includes('web-01'));
  assert.ok(N.options('Linux').includes('web-01')&&!N.options('Linux').includes('core-sw'));
  const choices=N.platformOptions('linux');
  assert.ok(choices.includes('<option value="linux" selected>Linux server</option>'));
  assert.equal((choices.match(/<option/g)||[]).length,3);
  assert.ok(!N.platformOptions(null,'firewall').includes('linux'));
  assert.equal(N.vendorOptions('Any'),'<option>Any</option><option>Cisco</option><option>MikroTik</option><option>Linux</option>');
  assert.ok(N.badge('linux').includes('class="badge linux"'));
});
