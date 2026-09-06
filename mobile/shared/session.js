/* A board owns controller state; each central owns its transport decoder.
 * Complete Aurora/Moon messages replace that board, never partial writes.
 * Quantum layers/roster are board-wide, continuation and CRC buffers are not. */
(function(root) {
  'use strict';
  class BoardSession {
    constructor(profile, api, emit, diagnostic=()=>{}) {
      this.profile=profile;this.api=api;this.emit=emit;this.diagnostic=diagnostic;
      this.peers=new Map();this.quantum={};this.stateValue=[1,71,0,0];
    }
    decoder(peer) {
      if(!this.peers.has(peer)) {
        const report=message=>this.diagnostic(peer+': '+message);
        const emit=holds=>this.emit(holds,peer);
        const p=this.profile;
        this.peers.set(peer,p.family==='aurora'?new root.BoardProtocols.Aurora(this.api,emit,report):
          p.family==='moonboard'?new root.BoardProtocols.Moon(p.rows,emit,report):
          new root.Quantum(p.addresses,emit,report,this.quantum));
      }
      return this.peers.get(peer);
    }
    feed(peer,bytes) {
      const updates=this.decoder(peer).feed(bytes);
      for(const update of updates)if(update.state)this.stateValue=update.state;
      return updates;
    }
    disconnect(peer) { this.peers.delete(peer); }
  }
  root.BoardSession=BoardSession;
  if(typeof module!=='undefined')module.exports={BoardSession};
})(globalThis);
