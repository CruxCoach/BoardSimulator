'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {Aurora,Moon}=require('../shared/protocols.js');
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
      trace.family==='moonboard'?new Moon(trace.rows,emit):new Quantum(trace.addresses,emit);
    for(const write of trace.writes) {
      if(write===null){decoder.reset();continue;}
      for(let i=0;i<write.length;i+=chunkSize) replies.push(...decoder.feed(write.slice(i,i+chunkSize)));
    }
    const label=trace.id+' API '+trace.api+' chunks '+chunkSize;
    assert.deepEqual(states,trace.states,label+' state');
    assert.deepEqual(replies,trace.replies,label+' GATT replies');
    if(trace.family==='moonboard')assert.deepEqual(moonLEDs,trace.moonLEDs,label+' aux LED semantics');
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
console.log(`${checks} fragmented conformance replays passed; ${catalog.length} configurations, palettes, assets, isolation and buffer limits checked`);
