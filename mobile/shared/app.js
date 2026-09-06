/* Offline UI. Native callbacks and bridge calls are serialized on the UI thread. */
'use strict';
const $=id=>document.getElementById(id);
let selected,decoder,holds=[],picture=null,generation=0,active=false;
let stateValue=[1,71,0,0],lines=[],received=0;
function native(command,payload={}) {
  const message=JSON.stringify({command,...payload});
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
for(const id of ['board','layout','size']) $(id).onchange=()=>cascade(id);
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
  active=value; for(const id of ['board','layout','size','api','start']) $(id).disabled=value;
  if(!value) $('size').disabled=selected.size===null;
}
$('start').onclick=()=>{
  received=0; stateValue=[1,71,0,0]; holds=[];
  const emit=value=>{holds=Array.from(new Map(value.map(h=>[h[0],h])).values());draw();};
  decoder=selected.family==='aurora'?new BoardProtocols.Aurora(Number($('api').value),emit,log):
    selected.family==='moonboard'?new BoardProtocols.Moon(selected.rows,emit,log):new Quantum(selected.addresses,emit,log);
  const profile=JSON.parse(JSON.stringify(selected));
  if(profile.family==='aurora') profile.name=profile.name.replace(/@3$/,'@'+$('api').value);
  delete profile.points;delete profile.roles;delete profile.addresses;
  $('identity').textContent=profile.name+' · '+profile.advertised;
  setActive(true); native('start',{profile});
};
$('stop').onclick=()=>native('stop');
$('copy').onclick=()=>native('copy',{text:lines.join('\n')});
window.BLE={
  status(message,running) { $('status').textContent=message;log(message);if(typeof running==='boolean')setActive(running); },
  receive(bytes) {
    if(!decoder||!active)return;
    received+=bytes.length;log('RX '+bytes.length+' B ('+received+' total): '+bytes.map(b=>b.toString(16).padStart(2,'0')).join(''));
    const updates=decoder.feed(bytes);
    for(const update of updates) if(update.state) stateValue=update.state;
    return updates;
  },
  readState() { return stateValue; },
  disconnected() { if(decoder)decoder.reset();log('Transport reset; rendered controller state retained'); }
};
cascade('board');native('ready');
