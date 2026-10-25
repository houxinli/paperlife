import test from 'node:test';
import assert from 'node:assert/strict';
import {ListeningCall} from './listening-audio.js';

async function withCall(run){
  const original={WebSocket:globalThis.WebSocket,location:globalThis.location};
  globalThis.location={protocol:'https:',host:'example.test',pathname:'/life/'};
  globalThis.WebSocket=class{static OPEN=1;constructor(){this.readyState=1;this.sent=[];}send(data){this.sent.push(data);}close(){this.wasClosed=true;}};
  const events=[],call=new ListeningCall(e=>events.push(e));
  call.context={state:'running',close:async()=>{}};call.connect('fiction-ticket');
  const send=e=>call.ws.onmessage({data:e instanceof ArrayBuffer?e:JSON.stringify(e)});
  try{await run({call,events,send});}finally{call.dispose();Object.assign(globalThis,original);}
}
test('listening never schedules or forwards server audio, including audio start boundaries',async()=>{
  await withCall(({call,send,events})=>{
    let played=0;call.play=()=>played++;
    send({type:'ready'});send({type:'audio_start'});send(new ArrayBuffer(8));send({type:'transcript',role:'assistant',text:'must not display'});
    assert.equal(played,0);assert.equal(call.ready,true);
    assert.equal(events.some(e=>e.type==='audio_start'||e.type==='transcript'),false);
    send({type:'listening_transcript',segments:[{id:'s',speaker_id:'speaker_1',text:'虚构内容'}]});
    assert.equal(events.at(-1).type,'listening_transcript');
  });
});
test('stop ends capture immediately, sends once, and waits for final transcript before ended',async()=>{
  await withCall(({call,send,events})=>{
    let stops=0;call.stream={getTracks:()=>[{stop(){stops++;}}]};send({type:'ready'});
    call.stop();call.stop();assert.equal(stops,1);assert.equal(call.ready,false);assert.equal(call.closed,false);
    assert.deepEqual(call.ws.sent.map(JSON.parse),[{type:'stop'}]);
    send({type:'ready'});assert.equal(call.ready,false);
    send({type:'listening_transcript',segments:[],final:true});send({type:'saved'});send({type:'ended'});
    assert.equal(call.closed,true);assert.equal(events.at(-1).type,'ended');
  });
});
test('connection loss reports incomplete save without synthetic ended',async()=>{
  await withCall(({call,events})=>{call.stop();call.ws.onclose();assert.ok(events.some(e=>e.type==='disconnected'));assert.equal(events.some(e=>e.type==='ended'),false);});
});
test('shared capture sends 16k packets only after ready and stops on browser suspension',async()=>{
  const original={AudioContext:globalThis.AudioContext,AudioWorkletNode:globalThis.AudioWorkletNode,navigator:globalThis.navigator};let stopped=0;
  const node=()=>({connect(){},disconnect(){}});
  globalThis.AudioContext=class{constructor(){this.state='running';this.destination={};this.audioWorklet={addModule:async()=>{}};}async resume(){}async close(){}createMediaStreamSource(){return node();}createGain(){return {...node(),gain:{}};}};
  globalThis.AudioWorkletNode=class{constructor(){this.port={};}connect(){}disconnect(){}};
  Object.defineProperty(globalThis,'navigator',{value:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stopped++;}}]})}},configurable:true});
  try{await withCall(async({call,send})=>{
    await call.prepare();const pcm=new Int16Array(320).buffer;call.worklet.port.onmessage({data:pcm});assert.equal(call.ws.sent.length,0);
    send({type:'ready'});call.worklet.port.onmessage({data:pcm});assert.equal(call.ws.sent[0],pcm);
    call.context.state='suspended';call.context.onstatechange();assert.equal(stopped,1);assert.equal(call.stopping,true);call.worklet.port.onmessage({data:pcm});assert.equal(call.ws.sent.filter(x=>x instanceof ArrayBuffer).length,1);
  });}finally{globalThis.AudioContext=original.AudioContext;globalThis.AudioWorkletNode=original.AudioWorkletNode;Object.defineProperty(globalThis,'navigator',{value:original.navigator,configurable:true});}
});
