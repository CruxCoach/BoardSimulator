'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {Aurora,Moon,ShinyWall}=require('../shared/protocols.js');
const {Quantum,crc}=require('../shared/quantum.js');
const fixtures=require('./generated-fixtures.json');
const catalog=require('../shared/generated/catalog.json');
let checks=0;
assert.equal(crc(Array.from(Buffer.from('123456789'))),0x4b37);
for(const trace of fixtures) {
  for(const chunkSize of [1,7,20,512]) {
    const states=[],replies=[],moonLEDs=[];
    let decoder;
    const emit=holds=>{
      states.push(Array.from(new Map(holds.map(h=>[h[0],h])).values()).sort((a,b)=>a[0]-b[0]));
      if(trace.family==='moonboard')moonLEDs.push(decoder.leds);
    };
    decoder=trace.family==='aurora'?new Aurora(trace.api,emit):
      trace.family==='moonboard'?new Moon(trace.rows,emit):
      trace.family==='shinywall'?new ShinyWall(trace.holdCount,emit,()=>{},trace.shiny):new Quantum(trace.addresses,emit);
    for(const write of trace.writes) {
      if(write===null){decoder.reset();continue;}
      for(let i=0;i<write.length;i+=chunkSize) replies.push(...decoder.feed(write.slice(i,i+chunkSize)));
    }
    const label=trace.id+' API '+trace.api+' chunks '+chunkSize;
    assert.deepEqual(states,trace.states,label+' state');
    assert.deepEqual(replies,trace.replies,label+' GATT replies');
    if(trace.family==='moonboard')assert.deepEqual(moonLEDs,trace.moonLEDs,label+' aux LED semantics');
    if(trace.family==='shinywall')assert.deepEqual(decoder.events,trace.events,label+' '+JSON.stringify(trace.shiny)+' events');
    checks++;
  }
}
// Every generated configuration has valid, normalized geometry and all resources.
for(const item of catalog) {
  assert.ok(Object.keys(item.points).length>0,item.id);
  assert.ok(item.aspect>0,item.id);
  for(const point of Object.values(item.points)) assert.ok(point.every(Number.isFinite),item.id);
  if(item.image)assert.ok(fs.existsSync(path.join(__dirname,'../shared/generated',item.image)),item.image);
  if(item.family==='aurora')for(const role of item.roles) {
    for(const api of [2,3]) {
      // Compare role inversion against received Linux-encoded palette samples.
      const fixture=fixtures.find(t=>t.id===item.id&&t.api===api);
      const paletteFrame=fixture.states[fixture.states.length-2];
      assert.ok(paletteFrame.some(h=>JSON.stringify(h.slice(1))===JSON.stringify(role['rgb'+api])),item.id+' role '+role.id);
    }
  }
}
// Decoder instances and reset boundaries must not leak transport or controller state.
let a=[],b=[];
const q1=new Quantum([1],x=>a=x),q2=new Quantum([1],x=>b=x);
q1.feed(Array.from(Buffer.from('{"cmd":"TURN_ON_ALL","color":"#ff0000"}')));
assert.deepEqual(a,[[1,255,0,0]]);assert.deepEqual(b,[]);
q1.reset();assert.deepEqual(a,[[1,255,0,0]]);
const diagnostics=[];new Moon(18,()=>{},x=>diagnostics.push(x)).feed(new Array(5000).fill(108));
assert.ok(diagnostics.includes('Moon frame overflow'));
// Shiny Wall: one catalog entry per variant, no vendor image, schematic points.
const shiny=catalog.filter(item=>item.family==='shinywall');
assert.deepEqual(shiny.map(item=>[item.layout,item.holdCount,item.name]),[
  ['st1',311,'ShinyWall_STP_0001'],['st4',401,'ShinyWall_STF_0001'],['st2',475,'ShinyWall_STM_0001'],
  ['st5',611,'ShinyWall_STMF_0001'],['st0',239,'Shiny Wall ST0 (ShinyMini)']]);
for(const item of shiny) {
  assert.equal(item.image,undefined,item.id);
  assert.equal(item.notifyUuid,'beb5483e-36e1-4688-b7f5-ea07361b26a8');
  assert.equal(Object.keys(item.points).length,item.holdCount,item.id);
}
console.log(`${checks} fragmented conformance replays passed; ${catalog.length} configurations, palettes, assets, isolation and buffer limits checked`);
