import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

test('microphone worklet converts 48kHz to 16kHz PCM at correct duration and amplitude',()=>{
  let Processor;const packets=[];
  const context=vm.createContext({sampleRate:48000,AudioWorkletProcessor:class{constructor(){this.port={postMessage:buffer=>packets.push(buffer)};}},registerProcessor:(name,type)=>Processor=type});
  vm.runInContext(fs.readFileSync(new URL('./capture.js',import.meta.url),'utf8'),context);
  const p=new Processor();
  for(let i=0;i<375;i++)p.process([[new Float32Array(128).fill(.5)]]);
  assert.equal(packets.length,50);
  assert.equal(packets.reduce((n,b)=>n+b.byteLength,0),32000);
  assert.equal(new Int16Array(packets[0])[0],16384);
});

test('microphone worklet handles 44.1kHz without changing duration',()=>{
  let Processor;const packets=[];
  const context=vm.createContext({sampleRate:44100,AudioWorkletProcessor:class{constructor(){this.port={postMessage:buffer=>packets.push(buffer)};}},registerProcessor:(name,type)=>Processor=type});
  vm.runInContext(fs.readFileSync(new URL('./capture.js',import.meta.url),'utf8'),context);
  new Processor().process([[new Float32Array(44100).fill(-1)]]);
  assert.equal(packets.length,50);assert.equal(new Int16Array(packets[0])[0],-32768);
});

test('stopping a reply suppresses in-flight audio until a new answer',async()=>{
  const {VoiceCall}=await import('./audio.js');
  const oldWs=globalThis.WebSocket,oldLocation=globalThis.location;
  globalThis.location={protocol:'https:',host:'example.test'};
  globalThis.WebSocket=class{static OPEN=1;close(){};send(){}};
  try{
    const call=new VoiceCall(()=>{});let played=0;call.play=()=>played++;
    call.connect('ticket');call.ws.onmessage({data:JSON.stringify({type:'ready'})});
    call.ws.onmessage({data:new ArrayBuffer(2)});assert.equal(played,1);
    call.stopAnswer();call.ws.onmessage({data:new ArrayBuffer(2)});assert.equal(played,1);
    call.ws.onmessage({data:JSON.stringify({type:'answer_start',reply_id:'next'})});
    call.ws.onmessage({data:new ArrayBuffer(2)});assert.equal(played,2);call.dispose();
  }finally{globalThis.WebSocket=oldWs;globalThis.location=oldLocation;}
});
