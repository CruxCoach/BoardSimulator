/* Streaming Aurora, MoonBoard and Shiny Wall decoders. Wire behavior follows Python;
 * output is [address,r,g,b] for both renderers. No native/UI dependencies. */
(function (root) {
  'use strict';
  class Aurora {
    constructor(api, emit, diagnostic = () => {}) {
      this.api = api; this.emit = emit; this.diagnostic = diagnostic; this.reset();
    }
    reset() { this.packet = []; this.holds = []; this.done = false; }
    feed(bytes) {
      for (const b of bytes) {
        if (this.done) { this.done = false; this.holds = []; }
        if (!this.packet.length && b !== 1) continue;
        this.packet.push(b);
        if (this.packet.length < 2 || this.packet.length !== this.packet[1] + 5) continue;
        const p = this.packet, v2 = this.api < 3, first = v2 ? [78,80] : [82,84];
        const sum = p.slice(4,-1).reduce((a,b) => a+b,0);
        if (p.length < 6 || p[3] !== 2 || p[p.length-1] !== 3 || ((~sum)&255) !== p[2] ||
            (this.holds.length === 0) !== first.includes(p[4])) {
          this.holds = []; this.diagnostic('Aurora framing/checksum/order rejected');
        } else {
          const width = v2 ? 2 : 3;
          for (let i=5; i+width <= p.length-1; i+=width) {
            const c = p[i+width-1];
            this.holds.push(v2 ? [p[i]+((c&3)<<8), ((c>>6)&3)*85, ((c>>4)&3)*85, ((c>>2)&3)*85] :
              [p[i]+(p[i+1]<<8), Math.floor((c>>5)/7*255), Math.floor(((c>>2)&7)/7*255), (c&3)*85]);
          }
          this.done = (v2 ? [79,80] : [83,84]).includes(p[4]);
          if (this.done) this.emit(this.holds.slice());
        }
        this.packet = [];
      }
      return [];
    }
  }
  const moonRoles = {s:[42,0,255,0], r:[43,0,0,255], p:[43,0,0,255],
    e:[44,255,0,0], f:[45,0,255,255], l:[47,128,0,255], m:[48,255,0,160]};
  class Moon {
    constructor(rows, emit, diagnostic = () => {}) {
      this.rows=rows; this.emit=emit; this.diagnostic=diagnostic; this.reset();
    }
    reset() { this.state=0; this.payload=''; this.aux=false; this.length=0; }
    feed(bytes) {
      for (const b of bytes) {
        const c=String.fromCharCode(b);
        if (++this.length > 4096) { this.reset(); this.diagnostic('Moon frame overflow'); }
        if (c==='~') { this.state=1; this.payload=''; this.aux=false; }
        else if (this.state===0 && c==='l') { this.state=2; this.aux=false; }
        else if (this.state===1) { if(c==='D') this.aux=true; if(c==='l') this.state=2; }
        else if (this.state===2) { if(c==='#') { this.state=3; this.payload=''; } else if(c==='l') this.aux=false; }
        else if (this.state===3) {
          if(c==='#') { this.finish(); this.reset(); } else this.payload+=c;
        }
      }
      return [];
    }
    finish() {
      const holds=[], details=[], leds=[];
      for (const token of this.payload.split(',')) {
        const m=/^([srpeflm])(\d+)/i.exec(token.trim());
        if(!m) continue;
        const n=Number(m[2]); if(n>=11*this.rows) continue;
        const col=Math.floor(n/this.rows), row=col%2 ? this.rows-1-n%this.rows : n%this.rows;
        const role=moonRoles[m[1].toLowerCase()], rgb=role.slice(1);
        holds.push([row*11+col+1,...rgb]); details.push([role[0],col,row,...rgb]);
        leds.push([n,...rgb]);
        // Original aux offset table walks 18-row columns even on Mini.
        const offset=n%18===0 || n%18===17 ? 0 : (Math.floor(n/18)%2 ? -1 : 1);
        if(this.aux && m[1].toLowerCase()!=='e' && offset) leds.push([n+offset,255,255,0]);
      }
      this.details=details; this.leds=leds; this.emit(holds);
    }
  }
  /* Shiny Wall: '-' clears; '<command><payload>.' otherwise, '+' + one digit
   * per hold + '.' sets the wall. Mirrors protocols/shinywall.py byte by
   * byte (same event kinds, same states however the stream is split). */
  const SHINY_RGB={2:[34,221,68],4:[58,140,255],6:[255,210,31],8:[255,59,59]};
  const SHINY_UNKNOWN_RGB=[255,255,255];
  const SHINY_KNOWN=new Set([0,2,4,6,8]);
  const SHINY_UNDERSTOOD=new Set(['clear','state','hold','disco','game','foot']);
  // '*<r><id>.' role digits (the app's route editor) -> vector codes.
  const SHINY_EDITOR=[0,2,4,6,8];
  const SHINY_ROLE_NAMES={2:'start',4:'hand',6:'foot',8:'top'};
  const DASH=45,PLUS=43,STAR=42,FOOT=102,DOT=46;
  function ascii(bytes) {
    return Array.from(bytes.slice(0,48),b=>b>=32&&b<127?String.fromCharCode(b):'\\x'+b.toString(16).padStart(2,'0')).join('')+
      (bytes.length>48?'… ('+bytes.length+' B)':'');
  }
  class ShinyWall {
    constructor(holdCount,emit,diagnostic=()=>{},options={}) {
      this.holdCount=holdCount;this.emit=emit;this.diagnostic=diagnostic;
      this.strict=options.length==='strict';this.accept=options.codes==='accept';this.ack=!!options.ack;
      this.limit=Math.max(4096,4*holdCount);this.events=[];
      // The wall a '*' edit changes; BoardSession shares it across centrals.
      this.shared=options.shared||{state:{}};
      this.command=null;this.discarding=false;this.payload=[];this.garbage=[];this.garbageTotal=0;
    }
    report(kind,detail) {
      this.events.push(kind);this.diagnostic(kind.toUpperCase()+' '+detail);
      if(this.ack&&SHINY_UNDERSTOOD.has(kind))this.updates.push({notify:[79,75]});
    }
    publish(state) {
      this.shared.state=state;
      this.emit(Object.keys(state).map(Number).sort((a,b)=>a-b).map(i=>[i,...(SHINY_RGB[state[i]]||SHINY_UNKNOWN_RGB)]));
    }
    endGarbage() {
      if(!this.garbageTotal)return;
      this.report('garbage',this.garbageTotal+' B outside a message: '+ascii(this.garbage));
      this.garbage=[];this.garbageTotal=0;
    }
    what() { return this.command===PLUS?'a state vector':this.command===STAR?"a '*' command":this.command===FOOT?"an 'f' command":
      "a '"+String.fromCharCode(this.command)+"' message"; }
    partial() { return (this.payload.length+1)+' B '+ascii([this.command,...this.payload]); }
    clear() { this.report('clear',"'-': all holds off");this.publish({}); }
    feed(bytes) {
      this.updates=[];
      for(const b of bytes)this.byte(b);
      return this.updates;
    }
    flush() { this.updates=[];this.endGarbage();return this.updates; }
    reset() {
      this.updates=[];this.endGarbage();
      if(this.command!==null)this.report('abort','link ended inside '+this.what()+'; discarded '+this.partial());
      this.command=null;this.discarding=false;this.payload=[];
      return this.updates;
    }
    byte(b) {
      if(b===DASH) {
        this.endGarbage();
        if(this.command!==null&&!this.discarding)this.report('abort',"'-' inside "+this.what()+'; discarded '+this.partial());
        this.command=null;this.discarding=false;this.payload=[];this.clear();return;
      }
      if(this.command===null) {
        if(b>=0x21&&b<=0x7e&&b!==DOT) {this.endGarbage();this.command=b;this.payload=[];}
        else {this.garbageTotal++;if(this.garbage.length<48)this.garbage.push(b);}
        return;
      }
      if(b===DOT) {
        const command=this.command,payload=this.payload,discarding=this.discarding;
        this.command=null;this.discarding=false;this.payload=[];
        if(!discarding)this.dispatch(command,payload);
        return;
      }
      if(this.discarding)return;
      this.payload.push(b);
      if(this.payload.length>this.limit) {
        this.report('overflow',this.what()+' longer than '+this.limit+" B without a closing '.'; discarded up to its end");
        this.discarding=true;this.payload=[];
      }
    }
    dispatch(command,payload) {
      if(command===PLUS)this.vector(payload);
      else if(command===STAR)this.single(payload);
      else if(command===FOOT)this.report('foot','init foot LEDs f'+ascii(payload)+'. (meaning unknown; logged, not applied)');
      else if(command>=48&&command<=57&&payload.includes(PLUS))this.report('legacy',"old dotted format; the controller expects '+', one digit per hold, '.'; ignored");
      else this.report('command',"unknown command '"+String.fromCharCode(command)+"' with payload '"+ascii(payload)+"'; ignored");
    }
    single(payload) {
      const shown='*'+ascii(payload)+'.';
      if(payload.length===1&&payload[0]===64){this.report('disco',shown+' disco mode (maintenance; logged, not applied)');return;}
      if(payload.includes(47)){this.report('game',shown+' game command (format not confirmed; logged, not applied)');return;}
      if(payload.length<2||payload.some(b=>b<48||b>57)){this.report('single',shown+' not understood (expected *<role><id>., *@. or a game *a/b/….); ignored');return;}
      const role=payload[0]-48,index=Number(String.fromCharCode(...payload.slice(1)));
      if(role>=SHINY_EDITOR.length){this.report('unknown-code',shown+' role '+role+' unknown (0 off, 1 start, 2 hand, 3 foot, 4 top); ignored');return;}
      if(index>=this.holdCount){this.report('invalid',shown+' hold '+index+' is beyond the wall (holds 0..'+(this.holdCount-1)+'); ignored');return;}
      const wall=Object.assign({},this.shared.state),code=SHINY_EDITOR[role];
      if(code)wall[index]=code;else delete wall[index];
      this.report('hold',shown+' hold '+index+' -> '+(SHINY_ROLE_NAMES[code]||'off')+'; '+Object.keys(wall).length+' lit');
      this.publish(wall);
    }
    vector(payload) {
      const codes=[],invalid=[],unknown=[];
      payload.forEach((b,i)=>{
        if(b<48||b>57){invalid.push(i);codes.push(0);return;}
        let value=b-48;
        if(!SHINY_KNOWN.has(value)){unknown.push(i);if(!this.accept)value=0;}
        codes.push(value);
      });
      if(invalid.length)this.report('invalid',invalid.length+' invalid code(s), hold(s) left off: '+invalid.slice(0,12).join(', '));
      if(unknown.length)this.report('unknown-code',unknown.length+' unknown code(s), '+(this.accept?'kept as unknown role':'hold(s) left off')+': '+unknown.slice(0,12).join(', '));
      if(codes.length!==this.holdCount) {
        this.report('length','vector has '+codes.length+' codes, this wall has '+this.holdCount+' holds');
        if(this.strict){this.report('reject','strict length: vector rejected, wall unchanged');return;}
      }
      const state={};
      codes.slice(0,this.holdCount).forEach((c,i)=>{if(c)state[i]=c;});
      const count=role=>Object.values(state).filter(c=>c===role).length;
      this.report('state',Math.min(codes.length,this.holdCount)+' codes applied — start '+count(2)+', hand '+count(4)+', foot '+count(6)+', top '+count(8)+'; '+(this.holdCount-Object.keys(state).length)+' off');
      this.publish(state);
    }
  }
  root.BoardProtocols={Aurora,Moon,ShinyWall,SHINY_RGB};
  if(typeof module!=='undefined') module.exports=root.BoardProtocols;
})(globalThis);
