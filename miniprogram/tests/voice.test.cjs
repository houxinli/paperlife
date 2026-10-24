const test=require('node:test');const assert=require('node:assert/strict');
const {VoiceCall}=require('../lib/voice');
function fixture(platform='ios'){
  const recorder={starts:[],stops:0,start(options){this.starts.push(options);this.Start();},stop(){this.stops++;if(this.Stop)this.Stop({});}};
  for(const name of ['Start','Stop','Error','FrameRecorded','InterruptionBegin','Pause']){recorder['on'+name]=fn=>recorder[name]=fn;recorder['off'+name]=fn=>{if(recorder[name]===fn)delete recorder[name];};}
  const sources=[],sent=[],socket={send(o){sent.push(o.data);o.success();},close(){}};
  for(const name of ['Open','Message','Error','Close'])socket['on'+name]=fn=>socket[name]=fn;
  const context={currentTime:0,state:'running',destination:{},resume:async()=>{},close:async()=>{context.state='closed';},createBuffer(channels,n,rate){const data=new Float32Array(n);return {duration:n/rate,getChannelData:()=>data,rate,data};},createBufferSource(){const s={connect(){},start(time){s.time=time;},stop(){s.stopped=true;},disconnect(){}};sources.push(s);return s;}};
  const wx={getDeviceInfo:()=>({platform}),getRecorderManager:()=>recorder,createWebAudioContext:()=>context,connectSocket:()=>socket};
  return {wx,recorder,socket,sent,sources,context};
}
test('native ticket is first message; microphone starts only after ready and uses PCM16k mono',async()=>{
  const f=fixture(),events=[],call=new VoiceCall(f.wx,e=>events.push(e));await call.prepare();
  assert.equal(f.recorder.starts.length,0);call.connect('wss://fictional.example/api/life/live','fictional-ticket');f.socket.Open();
  assert.deepEqual(f.sent,['{"ticket":"fictional-ticket"}']);
  f.socket.Message({data:JSON.stringify({type:'ready'})});
  assert.equal(f.recorder.starts[0].format,'PCM');assert.equal(f.recorder.starts[0].sampleRate,16000);assert.equal(f.recorder.starts[0].numberOfChannels,1);
  f.recorder.FrameRecorded({frameBuffer:Uint8Array.from([0,0,1,0]).buffer,isLastFrame:false});assert.equal(f.sent[1].byteLength,4);
  call.dispose();assert.equal(f.recorder.stops,1);
});
test('ending stops actual microphone and prevents late PCM or audio from reopening the call',async()=>{
  const f=fixture(),call=new VoiceCall(f.wx,()=>{});await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');f.socket.Open();f.socket.Message({data:'{"type":"ready"}'});
  const frame=f.recorder.FrameRecorded;f.socket.Message({data:'{"type":"ending"}'});assert.equal(f.recorder.stops,1);
  frame({frameBuffer:new ArrayBuffer(8)});f.socket.Message({data:'{"type":"audio_start","reply_id":"late"}'});f.socket.Message({data:new ArrayBuffer(8)});
  assert.equal(f.sent.length,1);assert.equal(f.sources.length,0);call.dispose();
});
test('24k playback survives sentence boundaries, cancels on interrupt, rejects stale reply',async()=>{
  const f=fixture(),call=new VoiceCall(f.wx,()=>{});await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');f.socket.Open();
  const e=event=>f.socket.Message({data:JSON.stringify(event)}),pcm=()=>f.socket.Message({data:Uint8Array.from([0,128,0,64]).buffer});
  e({type:'audio_start',reply_id:'r1',question_id:'q1'});pcm();e({type:'sentence_end'});pcm();
  assert.equal(f.sources.length,2);assert.equal(f.sources[0].buffer.rate,24000);assert.deepEqual([...f.sources[0].buffer.data],[-1,.5]);
  e({type:'interrupt',question_id:'q2'});assert.ok(f.sources.every(s=>s.stopped));
  e({type:'audio_start',reply_id:'r1',question_id:'q1'});pcm();assert.equal(f.sources.length,2);
  e({type:'audio_start',reply_id:'r2',question_id:'q2'});pcm();assert.equal(f.sources.length,3);call.dispose();
});
test('desktop recording is explicitly blocked; closing before ready never starts microphone',async()=>{
  await assert.rejects(new VoiceCall(fixture('windows').wx,()=>{}).prepare(),/手机/);
  const f=fixture(),call=new VoiceCall(f.wx,()=>{});await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');call.dispose();f.socket.Message({data:'{"type":"ready"}'});assert.equal(f.recorder.starts.length,0);
});
test('system interruption really stops microphone and sends stop',async()=>{
  const f=fixture(),call=new VoiceCall(f.wx,()=>{});await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');f.socket.Open();f.socket.Message({data:'{"type":"ready"}'});
  f.recorder.InterruptionBegin();assert.equal(f.recorder.stops,1);assert.equal(f.sent.at(-1),'{"type":"stop"}');call.dispose();
});
test('natural ten minute recorder stop ends call instead of appearing to listen',async()=>{
  const f=fixture(),call=new VoiceCall(f.wx,()=>{});await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');f.socket.Open();f.socket.Message({data:'{"type":"ready"}'});
  f.recorder.Stop({duration:600000});assert.equal(f.sent.at(-1),'{"type":"stop"}');call.dispose();
});
test('cancel before socket opens still releases the user interface',async()=>{
  const f=fixture(),events=[],call=new VoiceCall(f.wx,e=>events.push(e));await call.prepare();call.connect('wss://fictional.example/api/life/live','fictional-ticket');call.stop();
  assert.ok(events.some(e=>e.type==='disconnected'));assert.equal(f.recorder.starts.length,0);
});
