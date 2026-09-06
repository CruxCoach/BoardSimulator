/* Quantum eWalls 2.0.14 / 1.44 transport and persistent controller state.
 * Replies follow the Linux compatibility model, not a hardware capture. */
(function(root) {
  'use strict';
  const names=['ACTIVATE_WALL','TURN_OFF_BY_ROUTE','TURN_OFF_BY_USER','BOARD_SWIPE',
    'TURN_OFF_ALL','CHANGE_ROUTE_PARAMS','REQUEST_USER_ROUTE_LIST','ACTIVATE_WALL_LED_ID'];
  const commands={}; names.forEach((n,i)=>{commands[n]=65+i;});
  Object.assign(commands,{TURN_ON_ALL:100,SET_START_HOLDS:101,SET_STEP_HOLDS:102,SET_FINISH_HOLDS:103});
  const limits={65:92,68:92,72:32,101:120,102:120,103:120};
  const fixed={66:21,67:21,69:8,70:25,100:9};
  const legacyOnly=[70,72,101,102,103];
  const read=(a,i,n=2)=>a.slice(i,i+n).reduce((v,b)=>v*256+b,0);
  const hex=a=>a.map(b=>b.toString(16).padStart(2,'0')).join('');
  const unhex=s=>s.match(/../g).map(x=>parseInt(x,16));
  function crc(a) {
    let c=65535;
    for(const b of a) { c^=b; for(let j=0;j<8;j++) c=(c>>1)^((c&1)?40961:0); }
    return c;
  }
  function valid(a,legacy=false) {
    const tail=a.slice(-2); if(legacy) tail.reverse();
    return read(tail,0)===crc(a.slice(0,-2));
  }
  class Quantum {
    constructor(addresses,emit,diagnostic=()=>{},shared={}) {
      this.addresses=addresses; this.emit=emit; this.diagnostic=diagnostic;
      for(const key of ['routes','users','editor','all','players']) {
        if(!shared[key])shared[key]=new Map();
        Object.defineProperty(this,key,{get:()=>shared[key],set:value=>{shared[key]=value;}});
      }
      this.reset();
    }
    reset() { this.buffer=[]; this.json=''; this.continuation=null; }
    feed(bytes) {
      const replies=[];
      if(!bytes.length) return replies;
      const text=String.fromCharCode(...bytes);
      if(this.json || /^[\s]*[\[{]/.test(text)) { this.feedJSON(text); return replies; }
      this.buffer.push(...bytes);
      if(this.buffer.length>4096) { this.buffer=[]; this.diagnostic('OVERFLOW'); return replies; }
      while(this.buffer.length) {
        const b=this.buffer;
        if(b[0]!==1) { b.shift(); this.diagnostic('PREFIX'); continue; }
        if(b.length<2) break;
        const cmd=b[1];
        if(!Object.values(commands).includes(cmd)) { b.splice(0,2); this.diagnostic('COMMAND'); continue; }
        let n=fixed[cmd],legacy=false,ok=false;
        if(cmd===71) {
          if(b.length<5) break;
          if(valid(b.slice(0,5))) n=5;
          else if(b.length<8) { if(b.length>5 && b[5]===1) n=5; else break; }
          else { n=8; legacy=true; }
          ok=valid(b.slice(0,n),legacy);
        } else {
          if([65,68,72].includes(cmd)) { if(b.length<=40) break; n=43+b[40]; }
          if(cmd>=101) { if(b.length<=7) break; n=10+b[7]; }
          if(b.length<n) break;
          if(!legacyOnly.includes(cmd) && valid(b.slice(0,n))) ok=true;
          else { legacy=true; ok=valid(b.slice(0,n),true); }
        }
        const frame=b.splice(0,n);
        if(!ok) { this.continuation=null; this.diagnostic('CRC'); replies.push({notify:[1,cmd|128,3]}); continue; }
        try {
          const action=this.decode(frame,legacy); this.apply(action); this.diagnostic('ACK '+cmd);
          if(!legacy) {
            this.track(action);
            replies.push({notify:this.broadcast(action),state:this.snapshot(71)});
          }
        } catch(e) { this.continuation=null; this.diagnostic('PAYLOAD '+e.message); replies.push({notify:[1,cmd|128,3]}); }
      }
      return replies;
    }
    decode(b,legacy) {
      const a={cmd:b[1],route:'',user:'',color:[0,0,0],duration:0,diodes:[],replace:true};
      const id=i=>legacy ? String.fromCharCode(...b.slice(i,i+16)).replace(/\0+$/,'') : hex(b.slice(i,i+16));
      let key=[a.cmd,'','',legacy];
      if([65,68,72].includes(a.cmd)) {
        a.route=id(2); a.user=id(18); a.color=b.slice(34,37); a.duration=read(b,37);
        const count=b[40],width=a.cmd===72?4:2;
        if(count%width || b.length!==43+count) throw Error('diode count');
        for(let i=41;i<b.length-2;i+=width) a.diodes.push(read(b,i,width));
        key=[a.cmd,a.route,a.user,legacy];
      } else if(a.cmd===66) a.route=id(2);
      else if(a.cmd===67) a.user=id(2);
      else if(a.cmd===70) { a.route=id(2); a.color=b.slice(18,21); a.duration=read(b,21); }
      else if(a.cmd===100 || a.cmd>=101) {
        a.color=b.slice(2,5); a.duration=read(b,5);
        if(a.cmd>=101) {
          if(b[7]%2 || b.length!==10+b[7]) throw Error('editor count');
          for(let i=8;i<b.length-2;i+=2) a.diodes.push(read(b,i));
        }
      }
      key=JSON.stringify(key); a.replace=this.continuation!==key;
      this.continuation=limits[a.cmd]===a.diodes.length ? key : null;
      return a;
    }
    apply(a) {
      const c=a.cmd, remove=r=>{this.routes.delete(r);this.users.delete(r);};
      if([65,68,72].includes(c)) {
        if(c===68) for(const [r,u] of this.users) if(u===a.user && r!==a.route) remove(r);
        if(a.replace || !this.routes.has(a.route)) this.routes.set(a.route,new Map());
        for(const d of a.diodes) this.routes.get(a.route).set(d,a.color);
        this.users.set(a.route,a.user);
      } else if(c===66) remove(a.route);
      else if(c===67) { for(const [r,u] of this.users) if(u===a.user) remove(r); }
      else if(c===69) { this.routes.clear(); this.users.clear(); this.editor.clear(); this.all.clear(); }
      else if(c===70) { for(const d of (this.routes.get(a.route)||new Map()).keys()) this.routes.get(a.route).set(d,a.color); }
      else if(c===100) this.all=new Map(this.addresses.map(d=>[d,a.color]));
      else if(c>=101) {
        if(a.replace || !this.editor.has(c)) this.editor.set(c,new Map());
        for(const d of a.diodes) this.editor.get(c).set(d,a.color);
      }
      const state=new Map(this.all);
      for(const layer of [...this.routes.values(),...this.editor.values()]) for(const [d,color] of layer) state.set(d,color);
      this.emit(Array.from(state,([d,color])=>[d,...color]));
    }
    track(a) {
      if([65,68].includes(a.cmd)) {
        if(a.cmd===68) for(const [r,p] of this.players) if(p.user===a.user && r!==a.route) this.players.delete(r);
        this.players.set(a.route,a);
      } else if(a.cmd===66) this.players.delete(a.route);
      else if(a.cmd===67) { for(const [r,p] of this.players) if(p.user===a.user) this.players.delete(r); }
      else if(a.cmd===69) this.players.clear();
    }
    snapshot(cmd) {
      const result=[1,cmd,this.players.size&255,0];
      for(const a of this.players.values()) result.push(...unhex(a.route),...unhex(a.user),a.duration>>8,a.duration&255,...a.color);
      return result;
    }
    broadcast(a) {
      if([65,68,71].includes(a.cmd)) return this.snapshot(a.cmd);
      if(a.cmd===67) return [1,67,...unhex(a.user),0,0,0];
      if(a.cmd===69) return [1,69,0,0,0,0];
      if(a.cmd===100) return [1,100,255];
      return null;
    }
    feedJSON(text) {
      this.json+=text;
      if(this.json.length>4096) { this.json=''; this.diagnostic('JSON_OVERFLOW'); return; }
      // Find complete top-level values, respecting quoted braces and escapes.
      while(this.json.trim()) {
        this.json=this.json.trimStart(); let depth=0,quoted=false,escape=false,end=0;
        for(let i=0;i<this.json.length;i++) {
          const c=this.json[i];
          if(quoted) { if(escape) escape=false; else if(c==='\\') escape=true; else if(c==='"') quoted=false; }
          else if(c==='"') quoted=true;
          else if(c==='{'||c==='[') depth++;
          else if(c==='}'||c===']') { if(--depth===0) { end=i+1; break; } }
        }
        if(!end) return;
        const value=this.json.slice(0,end); this.json=this.json.slice(end);
        try {
          const v=JSON.parse(value); if(!v || Array.isArray(v)) throw Error('object required');
          let cmd=v.command===undefined ? (v.cmd===undefined?65:v.cmd) : v.command;
          if(typeof cmd==='string') cmd=commands[cmd.toUpperCase()];
          if(!Object.values(commands).includes(cmd)) throw Error('command');
          const color=String(v.color||'#000000').replace(/^#/,'');
          if(!/^[0-9a-f]{6}$/i.test(color)) throw Error('color');
          const diodes=(v.diodes||[]).map(d=>{
            if(typeof d==='object') d=d.autocadId===undefined?(d.idLedNode===undefined?d.id:d.idLedNode):d.autocadId;
            const n=Number(d); if(!Number.isInteger(n)) throw Error('diode'); return n;
          });
          this.apply({cmd,route:String(v.routeId===undefined?(v.id||''):v.routeId).slice(0,16),
            user:String(v.userId||'').slice(0,16),color:unhex(color),diodes,replace:true});
          this.diagnostic('LEGACY_ACK');
        } catch(e) { this.diagnostic('JSON_PAYLOAD '+e.message); }
      }
    }
  }
  root.Quantum=Quantum;
  if(typeof module!=='undefined') module.exports={Quantum,crc};
})(globalThis);
