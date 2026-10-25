import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute the actual editor handler with delayed media/network boundaries.
function fixture(){
  const app=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
  const handler=app.slice(app.indexOf('async function listen(track,seek=0){'),app.indexOf("$('listen-user').onclick"));
  let resolveFetch;const fetched=new Promise(r=>resolveFetch=r);
  const editor={open:true};let plays=0,pauses=0;
  const player={hidden:true,pause(){pauses++;},async play(){plays++;},removeAttribute(){}};
  const env={editing:{id:'synthetic-session'},profile:{id:'synthetic-person'},call:null,listening:{active:false},audioUrl:null,
    BASE:'',token:'',fetch:async()=>({ok:true,blob:()=>fetched}),authorizedAudio:()=>fetched,
    URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL(){}},$:(id)=>id==='editor'?editor:player,
    playback:{capture:()=>0,assert(){if(!editor.open||env.listening.active)throw Error('播放已取消');},async play(p){this.assert();await p.play();this.assert();}}};
  vm.createContext(env);vm.runInContext(handler,env);
  return {env,editor,player,resolveFetch,play:()=>env.listen('assistant'),plays:()=>plays,pauses:()=>pauses};
}
test('delayed old editor audio cannot play after closing and starting listening',async()=>{
  const f=fixture();const pending=f.play();const rejected=assert.rejects(pending);
  f.editor.open=false;f.player.pause();f.env.listening.active=true;f.player.pause();f.resolveFetch({});
  await new Promise(r=>setImmediate(r));f.player.onloadedmetadata?.();
  await rejected;assert.equal(f.plays(),0);
});
test('recording start during metadata load cancels pending editor playback',async()=>{
  const f=fixture();f.resolveFetch({});const pending=f.play();await new Promise(r=>setImmediate(r));
  f.env.listening.active=true;f.player.onloadedmetadata?.();
  await assert.rejects(pending);assert.equal(f.plays(),0);
});
test('ordinary authorized editor playback still seeks and plays',async()=>{
  const f=fixture();f.resolveFetch({});const pending=f.play();await new Promise(r=>setImmediate(r));
  f.player.onloadedmetadata();await pending;assert.equal(f.plays(),1);assert.equal(f.player.currentTime,0);
});
test('shared guard invalidates delayed playback even after recording has stopped',async()=>{
  const {createPlaybackGuard}=await import('./playback.js');let recording=false,plays=0,pauses=0;
  const player={pause(){pauses++;},async play(){plays++;}};
  const guard=createPlaybackGuard({isRecording:()=>recording,getPlayers:()=>[player]});
  const ticket=guard.capture();recording=true;guard.invalidate();recording=false;
  await assert.rejects(guard.play(player,ticket));assert.equal(plays,0);assert.equal(pauses,1);
  recording=true;guard.onPlay({target:player});assert.equal(pauses,2);
  recording=false;await guard.play(player,guard.capture());assert.equal(plays,1);
});
