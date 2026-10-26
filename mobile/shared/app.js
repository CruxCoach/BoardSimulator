/* Offline UI. Native callbacks and bridge calls are serialized on the UI thread. */
'use strict';
let nextRunToken=0;
function createPanel(element,slot) {
const $=id=>element.querySelector('[id="'+id+'"]');
let selected,session,holds=[],picture=null,generation=0,active=false;
let stateValue=[1,71,0,0],lines=[],received=0,runToken=0,guard=null;
function native(command,payload={}) {
  const message=JSON.stringify({command,...payload,slot});
  if(window.Native) window.Native.postMessage(message);
  else if(window.webkit && window.webkit.messageHandlers.ble) window.webkit.messageHandlers.ble.postMessage(message);
  else log('No native BLE bridge: browser preview cannot advertise.');
}
function log(message) {
  // Android: mirror every line into logcat (tag BoardSimUI) so a tester can follow a run over adb.
  if(window.Native && window.Native.log) window.Native.log(message);
  lines.push(new Date().toISOString()+' '+message); if(lines.length>180) lines.shift();
  $('log').textContent=lines.join('\n');
}
function options(id,items,value) {
  $(id).textContent='';
  for(const [key,label] of items) { const o=document.createElement('option'); o.value=key; o.textContent=label; $(id).appendChild(o); }
  if(value!==undefined) $(id).value=value;
}
function unique(items,key,label) { return Array.from(new Map(items.map(x=>[x[key],x[label]]))); }
options('board',unique(CATALOG,'board','boardName'));
function cascade(level) {
  const entries=CATALOG.filter(x=>x.board===$('board').value);
  if(level==='board') options('layout',unique(entries,'layout','layoutName'));
  const layouts=entries.filter(x=>x.layout===$('layout').value);
  if(level!=='size') options('size',layouts.map(x=>[String(x.size),x.sizeName]),String(layouts[0].defaultSize));
  selected=layouts.find(x=>String(x.size)===$('size').value)||layouts[0];
  for(const id of ['apiLabel','serialLabel','controllerLabel','strictLabel'])$(id).hidden=selected.family!=='aurora';
  for(const id of ['shinyNameLabel','shinyUuidLabel','shinyStrictLabel','shinyAckLabel'])$(id).hidden=selected.family!=='shinywall';
  if(selected.family==='shinywall')$('shinyName').value=selected.name;
  $('size').disabled=selected.size===null;
  showController();
  holds=[]; picture=null; const current=++generation;
  if(selected.image) {
    const image=new Image(); image.onload=()=>{if(current===generation){picture=image;draw();}};
    image.onerror=()=>log('Image unavailable: '+selected.image); image.src='generated/'+selected.image;
  }
  $('legend').textContent=selected.roles ? selected.roles.map(r=>r.name+' #'+r.id+' '+r.led_color).join(' · ') :
    selected.family==='moonboard' ? 'Start green · Hand blue · Finish red · Foot cyan · Left violet · Match pink' :
    selected.family==='shinywall' ? 'Start green · Hand blue · Foot yellow · Top red · '+selected.holdCount+' holds · positions: '+selected.positions :
    'Quantum route/editor colors';
  draw();
}
for(const id of ['board','layout','size']) $(id).onchange=()=>{const restart=active;cascade(id);if(restart)start();};
$('api').onchange=()=>{if(active)start();};
$('serial').onchange=()=>{if(active)start();};
for(const id of ['shinyName','shinyUuid','shinyStrict','shinyAck'])$(id).onchange=()=>{showController();if(active)start();};
function controller() { return selected.family==='aurora'?$('controller').value:'aurora'; }
function currentProfile() {
  if(selected.family==='shinywall') return shinyProfile(selected,{name:$('shinyName').value.trim()||undefined,
    advertiseUuid:$('shinyUuid').checked,strict:$('shinyStrict').checked,ack:$('shinyAck').checked});
  return controllerProfile(selected,controller(),$('serial').value,Number($('api').value));
}
function showController() {
  // A Kilter-built box has no serial and speaks API 2 only.
  const built=controller()==='kilter-built';
  if(built)$('api').value='2';
  $('api').disabled=built;$('serial').disabled=built;$('strict').disabled=!built;
  if(!built)$('strict').checked=false;
  try {const profile=currentProfile();$('identity').textContent=profile.name+' · '+(profile.advertised||'no service UUID advertised');}
  catch(error) {$('identity').textContent=error.message;}
}
$('controller').onchange=()=>{showController();if(active)start();};
$('strict').onchange=()=>{if(active)start();};
function draw() {
  const canvas=$('canvas'),ctx=canvas.getContext('2d'),box=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;
  canvas.width=Math.round(box.width*scale);canvas.height=Math.round(box.height*scale);ctx.scale(scale,scale);
  if(!selected) return;
  const w=Math.min(box.width,box.height*selected.aspect),h=w/selected.aspect,ox=(box.width-w)/2,oy=(box.height-h)/2;
  if(picture) ctx.drawImage(picture,ox,oy,w,h);
  else {ctx.fillStyle='#34434d';for(const p of Object.values(selected.points)){ctx.beginPath();ctx.arc(ox+p[0]*w,oy+p[1]*h,2,0,Math.PI*2);ctx.fill();}}
  let unknown=0;
  const shiny=selected.family==='shinywall';
  for(const [id,r,g,b] of holds) {
    const p=selected.points[id];if(!p){unknown++;continue;}
    const color='rgb('+[r,g,b].join(',')+')',radius=Math.max(4,shiny&&selected.cell?w*selected.cell*0.42:w/65);
    ctx.strokeStyle=color;ctx.lineWidth=Math.max(2,w/170);
    ctx.beginPath();ctx.arc(ox+p[0]*w,oy+p[1]*h,radius,0,Math.PI*2);ctx.stroke();
    // Shiny positions may be a placeholder: the index tells the tester which hold lit.
    if(shiny){ctx.fillStyle=color;ctx.font=Math.max(8,radius)+'px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(id),ox+p[0]*w,oy+p[1]*h);}
  }
  $('count').textContent=holds.length+' lit holds'+(unknown?' · '+unknown+' addresses outside this geometry':'');
}
window.addEventListener('resize',draw);
function setActive(value) {
  active=value; $('start').disabled=value;
  if(!value) $('size').disabled=selected.size===null;
}
$('connections').onchange=()=>native('connections',{multi:$('connections').value==='multi'});
function start() {
  if(selected.family==='aurora'&&controller()==='aurora'&&!$('serial').reportValidity())return;
  let profile;
  try {profile=currentProfile();} catch(error) {$('status').textContent=error.message;log(error.message);return;}
  received=0; stateValue=[1,71,0,0]; holds=[];
  const emit=value=>{holds=Array.from(new Map(value.map(h=>[h[0],h])).values());draw();};
  profile.runToken=runToken=++nextRunToken;
  session=new BoardSession(profile.shiny?Object.assign({},selected,{shiny:profile.shiny}):selected,
    profile.api||Number($('api').value),emit,log);
  guard=new WriteGuard(receivingFlags(profile),profile.minWriteGapMs||0,$('strict').checked,log);
  delete profile.points;delete profile.roles;delete profile.addresses;
  $('identity').textContent=profile.name+' · '+(profile.advertised||'no service UUID advertised');
  setActive(true); native('start',{profile,multi:$('connections').value==='multi'});
}
$('start').onclick=start;
$('stop').onclick=()=>native('stop');
$('resume').onclick=()=>native('release');
$('probe').onclick=()=>native('probe');
$('copy').onclick=()=>native('copy',{text:lines.join('\n')});
const api={
  dispose() {window.removeEventListener("resize",draw);generation++;picture=null;},
  canProbe() { $("probe").hidden=false; },
  canResume() { $("resume").hidden=false; },
  status(message,running) { $('status').textContent=message;log(message);if(typeof running==='boolean')setActive(running); },
  receive(bytes,peer="default",token=runToken,writeType=null,atMs=null) {
    if(!session||!active||token!==runToken)return [];
    if(guard&&!guard.accept(bytes.length,writeType,peer,atMs??performance.now()))return [];
    received+=bytes.length;
    const text=selected.family==='shinywall'&&bytes.every(b=>b>=32&&b<127)?" '"+String.fromCharCode(...bytes)+"'":'';
    log('RX '+bytes.length+' B ('+received+' total'+(writeType?', '+writeType:'')+') from '+peer+': '+bytes.map(b=>b.toString(16).padStart(2,'0')).join('')+text);
    const updates=session.feed(peer,bytes);
    for(const update of updates) if(update.state) stateValue=update.state;
    return updates;
  },
  readState() { return stateValue; },
  disconnected(peer='default') { if(session)session.disconnect(peer);log(peer+': transport reset; board state retained'); }
};
cascade('board');native('ready');return api;
}

const panels=document.getElementById('panels');
const template=panels.firstElementChild.cloneNode(true);
let panelAPIs=[];
window.BLE={
  status(text,running,slot=0) {if(panelAPIs[slot])panelAPIs[slot].status(text,running);},
  receive(bytes,peer,token,slot=0,writeType=null,atMs=null) {return panelAPIs[slot]?panelAPIs[slot].receive(bytes,peer,token,writeType,atMs):[];},
  readState(slot=0) {return panelAPIs[slot]?panelAPIs[slot].readState():[1,71,0,0];},
  disconnected(peer,slot=0) {if(panelAPIs[slot])panelAPIs[slot].disconnected(peer);},
  canProbe() {for(const panel of panelAPIs)panel.canProbe();},
  canResume() {for(const panel of panelAPIs)panel.canResume();},
  /* Android renames the whole phone for a board: its current Bluetooth name, and a
   * reset button while the phone's own name is out (or unknown). */
  phoneName(state) {
    document.getElementById('phoneName').hidden=false;
    document.getElementById('phoneNameText').textContent=state.text;
    const button=document.getElementById('resetName');
    button.hidden=!state.button;button.textContent=state.button||'';
  }
};
document.getElementById('resetName').onclick=()=>{if(window.Native)window.Native.postMessage(JSON.stringify({command:'resetName'}));};
function rebuild(count) {
  for(const api of panelAPIs)api.dispose();
  panels.textContent='';panelAPIs=[];
  for(let slot=0;slot<count;slot++) {
    const element=template.cloneNode(true);
    const title=document.createElement("h2");title.textContent="Board "+(slot+1);element.prepend(title);
    panels.appendChild(element);
    panelAPIs.push(createPanel(element,slot));
  }
}
rebuild(1);
document.getElementById('instances').onchange=event=>{
  const count=Number(event.target.value),message=JSON.stringify({command:'instances',count});
  if(window.Native)window.Native.postMessage(message);
  else if(window.webkit)window.webkit.messageHandlers.ble.postMessage(message);
  rebuild(count);
};
