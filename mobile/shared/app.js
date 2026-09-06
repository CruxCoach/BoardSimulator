/* Offline UI. Native callbacks and bridge calls are serialized on the UI thread. */
'use strict';
let nextRunToken=0;
function createPanel(element,slot) {
const $=id=>element.querySelector('[id="'+id+'"]');
let selected,session,holds=[],picture=null,generation=0,active=false;
let stateValue=[1,71,0,0],lines=[],received=0,runToken=0;
function native(command,payload={}) {
  const message=JSON.stringify({command,...payload,slot});
  if(window.Native) window.Native.postMessage(message);
  else if(window.webkit && window.webkit.messageHandlers.ble) window.webkit.messageHandlers.ble.postMessage(message);
  else log('No native BLE bridge: browser preview cannot advertise.');
}
function log(message) {
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
  $('apiLabel').hidden=selected.family!=='aurora'; $('size').disabled=selected.size===null;
  $('identity').textContent=selected.name+' · '+selected.advertised;
  holds=[]; picture=null; const current=++generation;
  if(selected.image) {
    const image=new Image(); image.onload=()=>{if(current===generation){picture=image;draw();}};
    image.onerror=()=>log('Image unavailable: '+selected.image); image.src='generated/'+selected.image;
  }
  $('legend').textContent=selected.roles ? selected.roles.map(r=>r.name+' #'+r.id+' '+r.led_color).join(' · ') :
    selected.family==='moonboard' ? 'Start green · Hand blue · Finish red · Foot cyan · Left violet · Match pink' : 'Quantum route/editor colors';
  draw();
}
for(const id of ['board','layout','size']) $(id).onchange=()=>{const restart=active;cascade(id);if(restart)start();};
$('api').onchange=()=>{if(active)start();};
function draw() {
  const canvas=$('canvas'),ctx=canvas.getContext('2d'),box=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;
  canvas.width=Math.round(box.width*scale);canvas.height=Math.round(box.height*scale);ctx.scale(scale,scale);
  if(!selected) return;
  const w=Math.min(box.width,box.height*selected.aspect),h=w/selected.aspect,ox=(box.width-w)/2,oy=(box.height-h)/2;
  if(picture) ctx.drawImage(picture,ox,oy,w,h);
  else {ctx.fillStyle='#34434d';for(const p of Object.values(selected.points)){ctx.beginPath();ctx.arc(ox+p[0]*w,oy+p[1]*h,2,0,Math.PI*2);ctx.fill();}}
  let unknown=0;
  for(const [id,r,g,b] of holds) {
    const p=selected.points[id];if(!p){unknown++;continue;}
    ctx.strokeStyle='rgb('+[r,g,b].join(',')+')';ctx.lineWidth=Math.max(2,w/170);
    ctx.beginPath();ctx.arc(ox+p[0]*w,oy+p[1]*h,Math.max(4,w/65),0,Math.PI*2);ctx.stroke();
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
  received=0; stateValue=[1,71,0,0]; holds=[];
  const emit=value=>{holds=Array.from(new Map(value.map(h=>[h[0],h])).values());draw();};
  session=new BoardSession(selected,Number($('api').value),emit,log);
  const profile=JSON.parse(JSON.stringify(selected)); profile.runToken=runToken=++nextRunToken;
  if(profile.family==='aurora') profile.name=profile.name.replace(/@3$/,'@'+$('api').value);
  delete profile.points;delete profile.roles;delete profile.addresses;
  $('identity').textContent=profile.name+' · '+profile.advertised;
  setActive(true); native('start',{profile,multi:$('connections').value==='multi'});
}
$('start').onclick=start;
$('stop').onclick=()=>native('stop');
$('resume').onclick=()=>native('release');
$('probe').onclick=()=>native('probe');
$('copy').onclick=()=>native('copy',{text:lines.join('\n')});
const api={
  canProbe() { $("probe").hidden=false; },
  canResume() { $("resume").hidden=false; },
  status(message,running) { $('status').textContent=message;log(message);if(typeof running==='boolean')setActive(running); },
  receive(bytes,peer="default",token=runToken) {
    if(!session||!active||token!==runToken)return [];
    received+=bytes.length;log('RX '+bytes.length+' B ('+received+' total): '+bytes.map(b=>b.toString(16).padStart(2,'0')).join(''));
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
  receive(bytes,peer,token,slot=0) {return panelAPIs[slot]?panelAPIs[slot].receive(bytes,peer,token):[];},
  readState(slot=0) {return panelAPIs[slot]?panelAPIs[slot].readState():[1,71,0,0];},
  disconnected(peer,slot=0) {if(panelAPIs[slot])panelAPIs[slot].disconnected(peer);},
  canProbe() {for(const panel of panelAPIs)panel.canProbe();},
  canResume() {for(const panel of panelAPIs)panel.canResume();}
};
function rebuild(count) {
  panels.textContent='';panelAPIs=[];
  for(let slot=0;slot<count;slot++) {
    const element=template.cloneNode(true);panels.appendChild(element);
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
