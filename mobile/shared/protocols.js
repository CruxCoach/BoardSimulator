/* Streaming Aurora and MoonBoard decoders. Wire behavior follows Python;
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
  root.BoardProtocols={Aurora,Moon};
  if(typeof module!=='undefined') module.exports=root.BoardProtocols;
})(globalThis);
