// Browser-only integration: Native is an explicit transport mock. This checks
// panel routing and lifecycle guards, not radio interoperability.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const fixtures=require('./generated-fixtures.json');
(async()=>{
  const browser=await chromium.launch({headless:true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{}),
    args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1100,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.messages=[];window.Native={postMessage:s=>window.messages.push(JSON.parse(s))};});
  await page.goto(pathToFileURL(path.join(__dirname,'../shared/index.html')).href);
  await page.waitForFunction(()=>typeof BLE!=='undefined');
  await page.selectOption('#instances','2');
  const panels=page.locator('.board-panel');assert.equal(await panels.count(),2);
  await panels.nth(0).locator('[id=board]').selectOption('moonboard');
  await panels.nth(1).locator('[id=board]').selectOption('quantum');
  await panels.nth(0).locator('[id=start]').click();
  await panels.nth(1).locator('[id=start]').click();
  const starts=await page.evaluate(()=>messages.filter(m=>m.command==='start'));
  assert.equal(starts[0].slot,0);assert.equal(starts[1].slot,1);
  await page.evaluate(()=>{BLE.status('Browser test: mock transport',true,0);BLE.status('Browser test: mock transport',true,1);});
  const q=fixtures.find(t=>t.id===starts[1].profile.id).writes[0];
  await page.evaluate(({starts,q})=>{
    BLE.receive(Array.from('l#S0,P1,E197#',c=>c.charCodeAt(0)),'moon-central',starts[0].profile.runToken,0);
    for(let i=0;i<q.length;i+=20)BLE.receive(q.slice(i,i+20),'quantum-central',starts[1].profile.runToken,1);
  },{starts,q});
  assert.equal(await panels.nth(0).locator('[id=count]').textContent(),'3 lit holds');
  assert.equal(await panels.nth(1).locator('[id=count]').textContent(),'92 lit holds');
  assert.equal(await panels.nth(0).locator('[id=apiLabel]').isVisible(),false);
  // Runtime switching resets only that endpoint and rejects old queued writes.
  await panels.nth(0).locator('[id=layout]').selectOption('mini-2020');
  await page.evaluate(token=>BLE.receive(Array.from('l#S0#',c=>c.charCodeAt(0)),'old',token,0),starts[0].profile.runToken);
  assert.equal(await panels.nth(0).locator('[id=count]').textContent(),'0 lit holds');
  assert.equal(await panels.nth(1).locator('[id=count]').textContent(),'92 lit holds');
  await panels.nth(1).locator('[id=connections]').selectOption('multi');
  const mode=await page.evaluate(()=>messages.at(-1));
  assert.deepEqual(mode,{command:'connections',multi:true,slot:1});
  // Kilter-built LED box: bare name, API 2 only, write-only RX, strict pacing drops a fast chunk.
  const kilter=panels.nth(0);
  await kilter.locator('[id=board]').selectOption('kilter');
  await kilter.locator('[id=controller]').selectOption('kilter-built');
  assert.equal(await kilter.locator('[id=api]').inputValue(),'2');
  assert.equal(await kilter.locator('[id=api]').isDisabled(),true);
  assert.equal(await kilter.locator('[id=serial]').isDisabled(),true);
  assert.match(await kilter.locator('[id=identity]').textContent(),/^Kilter Board · /);
  await kilter.locator('[id=strict]').check();
  const builtStart=await page.evaluate(()=>messages.filter(m=>m.command==='start'&&m.slot===0).at(-1));
  assert.equal(builtStart.profile.name,'Kilter Board');
  const rx=builtStart.profile.services.flatMap(s=>s.characteristics).find(c=>c.write);
  assert.deepEqual(rx.flags,['write']);
  const v2=fixtures.find(t=>t.id==='kilter/original/10'&&t.api===2);
  // The trace ends with two complete single-packet climbs and a clear.
  const message=v2.writes.at(-2),clear=v2.writes.at(-1);
  await page.evaluate(({message,clear,token})=>{
    BLE.status('Browser test: mock transport',true,0);
    BLE.receive(message,'kilter-central',token,0,'request',1000);
    BLE.receive(clear,'kilter-central',token,0,'request',1010);
  },{message,clear,token:builtStart.profile.runToken});
  assert.notEqual(await kilter.locator('[id=count]').textContent(),'0 lit holds','the clear 10 ms later must be dropped');
  assert.match(await kilter.locator('[id=log]').textContent(),/dropped a 6-byte write 10 ms/);
  // Shiny Wall: exact name, no advertised UUID, OK replies, ASCII receive log.
  const wall=panels.nth(1);
  await wall.locator('[id=board]').selectOption('shinywall');
  assert.equal(await wall.locator('[id=shinyNameLabel]').isVisible(),true);
  assert.equal(await wall.locator('[id=apiLabel]').isVisible(),false);
  assert.equal(await wall.locator('[id=shinyName]').inputValue(),'ShinyWall_STP_0001');
  await wall.locator('[id=shinyName]').fill('ShinyWall_STP_ABC123');
  await wall.locator('[id=shinyName]').dispatchEvent('change');
  await wall.locator('[id=shinyUuid]').uncheck();
  await wall.locator('[id=shinyAck]').check();  // a running panel restarts on every change
  const shinyStart=await page.evaluate(()=>messages.filter(m=>m.command==='start'&&m.slot===1).at(-1));
  assert.equal(shinyStart.profile.name,'ShinyWall_STP_ABC123');
  assert.equal(shinyStart.profile.advertised,'');
  assert.equal(shinyStart.profile.notifyUuid,'beb5483e-36e1-4688-b7f5-ea07361b26a8');
  assert.deepEqual(shinyStart.profile.shiny,{length:'lenient',codes:'reject',ack:true});
  assert.match(await wall.locator('[id=identity]').textContent(),/no service UUID advertised/);
  const replies=await page.evaluate(token=>{
    BLE.status('Browser test: mock transport',true,1);
    const vector=Array.from('+2'+'0'.repeat(150)+'4'+'0'.repeat(158)+'8.',c=>c.charCodeAt(0));
    const out=[BLE.receive([45],'shiny-central',token,1,'request',0)];
    for(let i=0;i<vector.length;i+=507)out.push(BLE.receive(vector.slice(i,i+507),'shiny-central',token,1,'request',100));
    return out;
  },shinyStart.profile.runToken);
  assert.deepEqual(replies,[[{notify:[79,75]}],[{notify:[79,75]}]]);
  assert.equal(await wall.locator('[id=count]').textContent(),'3 lit holds');
  assert.match(await wall.locator('[id=log]').textContent(),/RX 313 B \(314 total, request\) from shiny-central: 2b32.* '\+20+/);
  await wall.locator('[id=shinyName]').fill('ü'.repeat(15));  // 15 characters, 30 bytes
  await wall.locator('[id=shinyName]').dispatchEvent('change');
  assert.match(await wall.locator('[id=identity]').textContent(),/at most 29/);
  if(process.env.UI_SCREENSHOT)await page.screenshot({path:process.env.UI_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Two-panel UI: routed reception, isolated rendering, runtime switch generation guard, independent modes, the Kilter-built box and the Shiny Wall passed (mock transport only)');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
