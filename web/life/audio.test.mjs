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

async function withVoice(run){
  const {VoiceCall}=await import('./audio.js');
  const original={WebSocket:globalThis.WebSocket,location:globalThis.location};
  globalThis.location={protocol:'https:',host:'example.test'};
  globalThis.WebSocket=class{static OPEN=1;close(){};send(){}};
  const events=[],started=[];
  const context={state:'running',currentTime:0,destination:{},resume:async()=>{context.state='running';},close:async()=>{},
    createBuffer:(channels,length,rate)=>({duration:length/rate,getChannelData:()=>new Float32Array(length)}),
    createBufferSource:()=>({connect(){},start(time){started.push(this);this.time=time;},stop(){this.stopped=true;}})};
  const call=new VoiceCall(event=>events.push(event));call.context=context;call.connect('fictional-ticket');
  const send=event=>call.ws.onmessage({data:event instanceof ArrayBuffer?event:JSON.stringify(event)});
  send({type:'ready'});
  try{await run({call,context,events,started,send,audio:()=>send(new Int16Array(240).buffer)});}
  finally{call.dispose();Object.assign(globalThis,original);}
}

test('three rounds play when TTS starts before text and each reply has multiple sentences',async()=>{
  await withVoice(async({send,audio,started})=>{
    for(let round=1;round<=3;round++){
      const ids={question_id:`question-${round}`,reply_id:`reply-${round}`};
      send({type:'interrupt',question_id:ids.question_id});
      send({type:'audio_start',...ids});audio();
      send({type:'answer_start',source:'text',...ids});
      send({type:'audio_sentence_end',...ids});
      send({type:'audio_start',...ids});audio();
      send({type:'audio_end',...ids});
      assert.equal(started.length,round*2,`round ${round} must schedule both sentences`);
    }
  });
});

test('cancelled reply cannot resume from late text or another sentence, but next reply plays',async()=>{
  await withVoice(async({call,send,audio,started})=>{
    send({type:'audio_start',reply_id:'first',question_id:'q1'});audio();
    call.stopAnswer();assert.equal(started[0].stopped,true);
    send({type:'answer_start',source:'text',reply_id:'first',question_id:'q1'});audio();
    send({type:'audio_start',reply_id:'first',question_id:'q1'});audio();
    assert.equal(started.length,1,'cancelled reply must stay silent');
    send({type:'interrupt',question_id:'q2'});
    send({type:'audio_start',reply_id:'second',question_id:'q2'});audio();
    assert.equal(started.length,2);
  });
});

test('late old question boundaries and end events do not affect the new reply',async()=>{
  await withVoice(async({send,audio,started})=>{
    send({type:'audio_start',reply_id:'first',question_id:'q1'});audio();
    send({type:'interrupt',question_id:'q2'});
    send({type:'audio_start',reply_id:'late-old',question_id:'q1'});audio();
    assert.equal(started.length,1);
    send({type:'audio_start',reply_id:'second',question_id:'q2'});audio();
    send({type:'audio_end',reply_id:'first',question_id:'q1'});audio();
    assert.equal(started.length,3,'stale end must not suppress current audio');
    send({type:'audio_end',reply_id:'second',question_id:'q2'});audio();
    assert.equal(started.length,3,'late audio after whole reply end must be suppressed');
  });
});

test('suspended output resumes without creating another context or losing ordered PCM',async()=>{
  await withVoice(async({call,context,audio,started})=>{
    let resumes=0,finishResume;
    context.state='suspended';
    context.resume=()=>{resumes++;return new Promise(resolve=>{finishResume=()=>{context.state='running';resolve();};});};
    audio();audio();
    assert.equal(resumes,1,'one in-flight resume is shared');
    assert.equal(started.length,2);assert.ok(started[1].time>started[0].time);
    call.stopAnswer();finishResume();await Promise.resolve();await Promise.resolve();
    assert.ok(started.every(source=>source.stopped),'resuming must not resurrect cancelled audio');
    assert.equal(call.context,context);
  });
});

test('blocked audio resume surfaces recovery state and metadata without transcript text',async()=>{
  await withVoice(async({call,context,events,audio})=>{
    context.state='interrupted';context.resume=async()=>{throw new Error('browser requires gesture');};
    audio();await new Promise(resolve=>setImmediate(resolve));
    assert.ok(events.some(event=>event.type==='audio_state'&&event.state==='blocked'));
    assert.equal(call.diagnostics.resumeFailures,1);
    context.resume=async()=>{context.state='running';};
    await call.resumeAudio();
    assert.equal(context.state,'running');
    const diagnostic=events.filter(event=>event.type==='audio_diagnostic').at(-1);
    assert.equal(diagnostic.resumeFailures,1);
    assert.equal(diagnostic.contextState,'running');
    assert.equal('text' in diagnostic,false);assert.equal('reply_id' in diagnostic,false);
  });
});

test('a user gesture can retry while an automatic resume is still pending',async()=>{
  await withVoice(async({call,context,events,audio})=>{
    let attempts=0;
    context.state='suspended';
    context.resume=()=>{attempts++;if(attempts===1)return new Promise(()=>{});context.state='running';return Promise.resolve();};
    audio();
    assert.equal(events.filter(event=>event.type==='audio_state').at(-1)?.state,'blocked');
    assert.equal(await call.resumeAudio(),true);
    assert.equal(attempts,2);
  });
});

test('device suspension after the last packet exposes recovery without resetting the call',async()=>{
  await withVoice(async({call,context,events,audio})=>{
    audio();context.state='interrupted';context.resume=async()=>{throw new Error('gesture required');};
    assert.equal(typeof context.onstatechange,'function');
    context.onstatechange();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(events.filter(event=>event.type==='audio_state').at(-1).state,'blocked');
    assert.equal(call.closed,false);assert.equal(call.ready,true);
  });
});

test('an older resume failure cannot hide a successful gesture recovery',async()=>{
  await withVoice(async({call,context,events,audio})=>{
    let rejectFirst,attempts=0;
    context.state='suspended';
    context.resume=()=>{attempts++;if(attempts===1)return new Promise((resolve,reject)=>{rejectFirst=reject;});context.state='running';return Promise.resolve();};
    audio();assert.equal(await call.resumeAudio(),true);
    rejectFirst(new Error('superseded attempt'));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(events.filter(event=>event.type==='audio_state').at(-1).state,'playing');
  });
});

test('resume promise resolving while still suspended does not report playback recovery',async()=>{
  await withVoice(async({call,context,events})=>{
    context.state='suspended';context.resume=async()=>{};
    assert.equal(await call.resumeAudio(),false);
    assert.equal(events.filter(event=>event.type==='audio_state').at(-1).state,'blocked');
  });
});

test('server ending stops the microphone and playback while awaiting saved confirmation',async()=>{
  await withVoice(async({call,send,audio,events,started})=>{
    let stops=0,closed=0,sent=0;
    call.stream={getTracks:()=>[{stop(){stops++;}}]};
    call.ws.readyState=1;call.ws.close=()=>closed++;call.ws.send=()=>sent++;
    audio();
    send({type:'ending',reason:'narrator_request'});
    assert.equal(stops,1);assert.equal(call.ready,false);assert.equal(call.stopping,true);
    assert.equal(started[0].stopped,true);assert.equal(closed,0);assert.equal(call.closed,false);
    call.stop();send({type:'ending',reason:'narrator_request'});
    send({type:'ready'});send({type:'audio_start',reply_id:'late'});audio();
    assert.equal(call.ready,false);assert.equal(sent,0);assert.equal(stops,1);assert.equal(started.length,1);
    assert.equal(events.some(e=>e.type==='ended'),false);
    send({type:'ended',status:'ended'});
    assert.equal(call.closed,true);assert.equal(call.ended,true);assert.equal(closed,1);
  });
});

test('button stop is idempotent and disconnect during saving does not claim success',async()=>{
  await withVoice(async({call,events})=>{
    let sent=0;call.ws.readyState=1;call.ws.send=()=>sent++;
    call.stop();call.stop();
    assert.equal(sent,1);
    call.ws.onclose();
    assert.equal(events.some(e=>e.type==='ended'),false);
    assert.ok(events.some(e=>e.type==='disconnected'));
  });
});
