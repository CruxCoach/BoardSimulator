'use strict';
const assert=require('node:assert/strict');
require('../shared/protocols.js');require('../shared/quantum.js');
const {BoardSession}=require('../shared/session.js');
const fixtures=require('./generated-fixtures.json');
const catalog=require('../shared/generated/catalog.json');
const profile=catalog.find(p=>p.id==='kilter/original/10');
const frames=fixtures.find(t=>t.id===profile.id && t.api===3);
// Pick complete short messages from the independent Python trace.
const short=frames.writes[frames.writes.length-2],clear=frames.writes[frames.writes.length-1];
let output=[];
const board=new BoardSession(profile,3,h=>output.push(h));
board.feed('controller A',short.slice(0,4));
board.feed('controller B',clear);
assert.deepEqual(output,[[]],'B clear must not consume A partial frame');
board.feed('controller A',short.slice(4));
assert.deepEqual(output[1].sort((a,b)=>a[0]-b[0]),frames.states.at(-2));
board.feed('controller A',short.slice(0,3));
board.feed('controller B',short.slice(0,5));
board.disconnect('controller A');
board.feed('controller B',short.slice(5));
assert.equal(output.length,3,'Disconnecting A must not reset B');
const second=new BoardSession(profile,3,()=>assert.fail('Other board changed'));
assert.equal(second.peers.size,0);

for(const profile of catalog.filter(p=>p.family==='quantum')) {
  const trace=fixtures.find(t=>t.id===profile.id);
  let state=[];
  const first=new BoardSession(profile,3,h=>state=h);
  const independent=new BoardSession(profile,3,()=>assert.fail('Quantum board isolation'));
  const a=trace.writes[0],b=trace.writes[3];
  first.feed('A',a.slice(0,21));
  assert.deepEqual(state,[],'Partial route must not publish');
  first.feed('B',b);
  assert.equal(first.stateValue[2],1);
  first.feed('A',a.slice(21));
  assert.equal(first.stateValue[2],2,'Roster must be shared across controller decoders');
  assert.equal(first.stateValue.length,4+2*37);
  first.feed('A',trace.writes[1]);
  assert.equal(state.length,113,'92+18 continuation must survive another controller route');
  first.disconnect('B');
  first.feed('A',trace.writes[2]);
  assert.equal(first.stateValue[2],2,'Disconnect preserves board roster');
  assert.deepEqual(independent.stateValue,[1,71,0,0]);
}
console.log('Interleaved controllers: independent frame buffers, disconnect isolation, Quantum shared roster/layers and independent boards passed');
