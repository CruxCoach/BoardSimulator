/* A board owns controller state; each central owns its transport decoder.
 * Complete Aurora/Moon messages replace that board, never partial writes.
 * Quantum layers/roster are board-wide, continuation and CRC buffers are not. */
(function(root) {
  'use strict';
  class BoardSession {
    constructor(profile, api, emit, diagnostic=()=>{}) {
      this.profile=profile;this.api=api;this.emit=emit;this.diagnostic=diagnostic;
      this.peers=new Map();this.quantum={};this.shinyWall={state:{}};this.stateValue=[1,71,0,0];
    }
    decoder(peer) {
      if(!this.peers.has(peer)) {
        const report=message=>this.diagnostic(peer+': '+message);
        const emit=holds=>this.emit(holds,peer);
        const p=this.profile;
        this.peers.set(peer,p.family==='aurora'?new root.BoardProtocols.Aurora(this.api,emit,report):
          p.family==='moonboard'?new root.BoardProtocols.Moon(p.rows,emit,report):
          p.family==='shinywall'?new root.BoardProtocols.ShinyWall(p.holdCount,emit,report,Object.assign({},p.shiny,{shared:this.shinyWall})):
          new root.Quantum(p.addresses,emit,report,this.quantum));
      }
      return this.peers.get(peer);
    }
    feed(peer,bytes) {
      const updates=this.decoder(peer).feed(bytes);
      for(const update of updates)if(update.state)this.stateValue=update.state;
      return updates;
    }
    disconnect(peer) {
      // A Shiny Wall reports the message a link loss cut off, like Linux.
      const decoder=this.peers.get(peer);
      if(decoder&&decoder.reset&&this.profile.family==='shinywall')decoder.reset();
      this.peers.delete(peer);
    }
  }
  /* Shiny Wall knobs, mirroring the Linux --name / --no-advertised-uuid /
   * --shiny-length / --shiny-ack options. The exact name lets the official
   * app find the simulator (it connects to the first device whose name
   * contains the wall's stored name, "ShinyWall_" when none is stored). */
  const SCAN_NAME_LIMIT=29;
  function shinyProfile(selected,{name,advertiseUuid=true,strict=false,ack=false}={}) {
    const profile=JSON.parse(JSON.stringify(selected));
    if(profile.family!=='shinywall') return profile;
    if(name) {
      const size=new TextEncoder().encode(name).length;
      if(size>SCAN_NAME_LIMIT) throw new Error('Name is '+size+' bytes; a scan response carries at most '+SCAN_NAME_LIMIT);
      profile.name=name;
    }
    if(!advertiseUuid) profile.advertised='';
    profile.shiny={length:strict?'strict':'lenient',codes:'reject',ack};
    return profile;
  }
  /* Aurora LED box generations, mirroring the Linux --controller option.
   * A Kilter-built box (since mid-2025) advertises the bare product name,
   * speaks API 2 and takes write-with-response only; the Kilter app writes
   * it in 20-byte chunks 100 ms apart. */
  const KILTER_BUILT={api:2,gapMs:100};
  function controllerProfile(selected,controller,serial,api) {
    const profile=JSON.parse(JSON.stringify(selected));
    if(profile.family!=='aurora') return profile;
    if(controller==='kilter-built') {
      profile.name=profile.boardName;
      for(const s of profile.services)for(const c of s.characteristics)if(c.write)c.flags=['write'];
      Object.assign(profile,{controller:'kilter-built',api:KILTER_BUILT.api,minWriteGapMs:KILTER_BUILT.gapMs});
    } else {
      profile.name=profile.name.replace(/#0001@3$/,'#'+serial+'@'+api);
      Object.assign(profile,{controller:'aurora',api,minWriteGapMs:0});
    }
    return profile;
  }
  /* Same checks as the Linux WriteGuard: a write without response to a
   * write-only RX is reported once per central (iOS drops such writes);
   * with a gap set, chunks closer than 75 % of it within one burst (< 1 s)
   * are reported once per burst, or dropped in strict mode. */
  class WriteGuard {
    constructor(flags,minGapMs=0,dropUnpaced=false,report=()=>{}) {
      this.acceptsCommands=flags.includes('write-without-response');
      this.minGapMs=minGapMs;this.dropUnpaced=dropUnpaced;this.report=report;
      this.last=new Map();this.warnedBurst=new Set();this.warnedCommands=new Set();
      this.unpaced=0;this.dropped=0;
    }
    accept(length,writeType,peer,now) {
      if(writeType==='command'&&!this.acceptsCommands&&!this.warnedCommands.has(peer)) {
        this.warnedCommands.add(peer);
        this.report(peer+': writes without response to a write-only characteristic; iOS drops such writes, the Kilter app always writes with response');
      }
      if(this.minGapMs<=0)return true;
      const last=this.last.get(peer),gap=last===undefined?undefined:now-last;
      if(gap===undefined||gap>=1000) this.warnedBurst.delete(peer);
      else if(gap<this.minGapMs*0.75) {
        this.unpaced++;
        if(this.dropUnpaced) {
          this.dropped++;this.report(peer+': dropped a '+length+'-byte write '+Math.round(gap)+' ms after the previous one (strict pacing, '+this.minGapMs+' ms expected)');
          return false;
        }
        if(!this.warnedBurst.has(peer)) {
          this.warnedBurst.add(peer);this.report(peer+': chunks '+Math.round(gap)+' ms apart; this controller expects '+this.minGapMs+' ms (the Kilter app\'s pacing)');
        }
      }
      this.last.set(peer,now);
      return true;
    }
  }
  function receivingFlags(profile) {
    for(const s of profile.services||[])for(const c of s.characteristics)if(c.write)return c.flags;
    return [];
  }
  root.BoardSession=BoardSession;root.controllerProfile=controllerProfile;
  root.WriteGuard=WriteGuard;root.receivingFlags=receivingFlags;root.shinyProfile=shinyProfile;
  if(typeof module!=='undefined')module.exports={BoardSession,controllerProfile,WriteGuard,receivingFlags,shinyProfile};
})(globalThis);
