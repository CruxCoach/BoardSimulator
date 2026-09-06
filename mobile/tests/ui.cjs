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
  if(process.env.UI_SCREENSHOT)await page.screenshot({path:process.env.UI_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Two-panel UI: routed reception, isolated rendering, runtime switch generation guard and independent modes passed (mock transport only)');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
