const test = require('node:test');
const assert = require('node:assert/strict');
const { PCMStream, pcmToFloat } = require('../lib/pcm');
const { AudioGate, OrderedSender, updateTranscript } = require('../lib/protocol');
const { Api, validateConfig } = require('../lib/api');

function bytes(...values) { return Uint8Array.from(values).buffer; }
function wav(rate = 16000, channels = 1, bits = 16) {
  const b = new ArrayBuffer(48), v = new DataView(b), u = new Uint8Array(b);
  for (const [at, text] of [[0,'RIFF'],[8,'WAVE'],[12,'fmt '],[36,'data']]) for(let i=0;i<text.length;i++) u[at+i]=text.charCodeAt(i);
  v.setUint32(4,40,true); v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,channels,true);
  v.setUint32(24,rate,true); v.setUint32(28,rate*channels*bits/8,true); v.setUint16(32,channels*bits/8,true); v.setUint16(34,bits,true); v.setUint32(40,4,true);
  v.setInt16(44,-32768,true); v.setInt16(46,16384,true); return b;
}
test('PCM preserves sample boundaries across odd transport chunks', () => {
  const p = new PCMStream();
  assert.equal(p.push(bytes(0)).byteLength,0);
  assert.deepEqual([...new Uint8Array(p.push(bytes(128,0,64)))],[0,128,0,64]);
  assert.deepEqual([...pcmToFloat(bytes(0,128,0,64,255,127))],[-1,.5,32767/32768]);
  p.finish();
});
test('PCM refuses a truncated final sample and compressed formats', () => {
  for (const b of [bytes(73,68,51,0),bytes(255,251,0,0),bytes(255,241,0,0),bytes(79,103,103,83)]) assert.throws(()=>new PCMStream().push(b),/格式/);
  const p=new PCMStream();p.push(bytes(0,0,0));assert.throws(()=>p.finish(),/完整/);
});
test('WAV adapter validates format and strips headers split across frames', () => {
  const p=new PCMStream(), data=wav();
  assert.equal(p.push(data.slice(0,18)).byteLength,0);
  assert.deepEqual([...new Uint8Array(p.push(data.slice(18)))],[0,128,0,64]); p.finish();
  for(const data of [wav(24000),wav(16000,2),wav(16000,1,8)]) assert.throws(()=>new PCMStream().push(data),/16000/);
});
test('audio boundaries reject cancelled and completed replies; sentence end leaves the gate open', () => {
  const gate=new AudioGate();assert.equal(gate.accepts(),false);
  gate.event({type:'audio_start',reply_id:'old',question_id:'q1'});assert.equal(gate.accepts(),true);
  gate.event({type:'audio_sentence_end',reply_id:'old'});assert.equal(gate.accepts(),true);
  gate.event({type:'interrupt',question_id:'q2'});assert.equal(gate.accepts(),false);
  gate.event({type:'audio_start',reply_id:'old',question_id:'q1'});assert.equal(gate.accepts(),false);
  gate.event({type:'answer_start',reply_id:'new',question_id:'q2',source:'text'});assert.equal(gate.accepts(),false);
  gate.event({type:'audio_start',reply_id:'new',question_id:'q2'});assert.equal(gate.accepts(),true);
  gate.event({type:'audio_end',reply_id:'old',question_id:'q1'});assert.equal(gate.accepts(),true);
  gate.event({type:'audio_end',reply_id:'new',question_id:'q2'});assert.equal(gate.accepts(),false);
  gate.event({type:'audio_start',reply_id:'new',question_id:'q2'});assert.equal(gate.accepts(),false);
});
test('ending permanently closes audio input/output gate', () => {
  const gate=new AudioGate();gate.event({type:'ending'});gate.event({type:'audio_start',reply_id:'late'});assert.equal(gate.accepts(),false);
});
test('transcripts replace partial text, preserve final turns, and separate speakers', () => {
  let rows=[];
  for(const event of [{role:'user',text:'虚构',final:false},{role:'assistant',text:'您说',final:false},{role:'user',text:'虚构故事。',final:true},{role:'user',text:'第二段',final:false}]) rows=updateTranscript(rows,event);
  assert.deepEqual(rows.map(r=>[r.role,r.text,r.final]),[['user','虚构故事。',true],['assistant','您说',false],['user','第二段',false]]);
});
test('interrupt preserves unfinished assistant text and the next answer gets a new row', () => {
  const before=updateTranscript([], {type:'transcript',role:'assistant',text:'虚构的前半句',final:false});
  const interrupted=updateTranscript(before, {type:'interrupt',question_id:'q2'});
  const next=updateTranscript(interrupted, {type:'transcript',role:'assistant',text:'我们接着聊',final:false});
  assert.equal(before[0].final,false,'the previously rendered snapshot is not mutated');
  assert.deepEqual(next.map(row=>[row.text,row.final,row.interrupted||false]), [
    ['虚构的前半句',true,true],['我们接着聊',false,false]
  ]);
  assert.notEqual(next[0].id,next[1].id);
});
test('socket writes stay ordered and cancellation removes pending audio', () => {
  const sent=[], pending=[], errors=[];
  const q=new OrderedSender({send(o){sent.push(o.data);pending.push(o.success);}},e=>errors.push(e),{timeout:1000});
  q.send('{"ticket":"fictional"}');q.send(bytes(1,2));q.send(bytes(3,4));
  assert.equal(sent.length,1);pending.shift()();assert.equal(sent.length,2);
  q.dropAudio();q.send('{"type":"stop"}');pending.shift()();
  assert.deepEqual(sent.filter(x=>typeof x==='string'),['{"ticket":"fictional"}','{"type":"stop"}']);assert.equal(sent.length,3);
  pending.shift()();q.close();assert.equal(errors.length,0);
});
test('network backlog fails explicitly instead of silently dropping accepted audio', () => {
  const errors=[];const q=new OrderedSender({send(){}},e=>errors.push(e),{maxBytes:8,timeout:1000});
  q.send(bytes(0,0,0,0,0,0));q.send(bytes(0,0,0,0));assert.equal(errors.length,1);q.close();
});
function wxFixture() {
  const storage=new Map(),requests=[];
  return {requests,storage,getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    login(o){o.success({code:'fictional-login-code'});},request(o){requests.push(o);if(o.url.endsWith('/auth/wechat'))o.success({statusCode:200,data:{session_id:'fictional-session',user:{id:7}}});else o.success({statusCode:200,data:[]});}};
}
const config={apiBaseUrl:'https://fictional.example/api'}, appId='wx1111111111111111';
test('configuration cannot silently use tourist appid or plaintext endpoints', () => {
  for (const [c,id] of [[{},appId],[config,'touristappid'],[{apiBaseUrl:'http://fictional.example/api'},appId],[{apiBaseUrl:'https://someone:secret@fictional.example/api'},appId]]) assert.throws(()=>validateConfig(c,id));
  assert.equal(validateConfig(config,appId).socketUrl,'wss://fictional.example/api/life/live');
});
test('wechat code exchange yields bearer only; invite remains separate and guest scoped', async () => {
  const wx=wxFixture(),api=new Api(wx,config,appId);api.acceptInvite({invite:'fictional_invite_123456789'});
  await api.login();assert.deepEqual(wx.requests[0].data,{code:'fictional-login-code'});
  assert.equal(wx.requests[0].header.Authorization,undefined);assert.equal(wx.requests[0].header['X-Life-Link'],undefined);
  await api.request('/life/profiles');assert.equal(wx.requests[1].header.Authorization,'Bearer fictional-session');
  await api.request('/life/guest/history','GET',null,'guest');assert.equal(wx.requests[2].header['X-Life-Link'],'fictional_invite_123456789');assert.equal(wx.requests[2].header.Authorization,undefined);
  const next=new Api(wx,config,appId);assert.equal(next.invite,'fictional_invite_123456789');assert.equal(next.session,'fictional-session');
  assert.throws(()=>next.headers('guest','/life/profiles'),/专属/);
});
test('invalid incoming invite clears old capability rather than silently opening another person', () => {
  const api=new Api(wxFixture(),config,appId);api.acceptInvite({invite:'fictional_invite_123456789'});
  assert.throws(()=>api.acceptInvite({invite:'bad'}),/无效/);assert.equal(api.invite,'');
});
test('credentials are namespaced by server and app id', async () => {
  const wx=wxFixture(),api=new Api(wx,config,appId);await api.login();
  assert.equal(new Api(wx,{apiBaseUrl:'https://other-fictional.example/api'},appId).session,'');
});
test('server rejected login does not produce a logged in state', async () => {
  const wx=wxFixture();wx.request=o=>o.success({statusCode:503,data:{detail:'微信登录尚未开通'}});
  const api=new Api(wx,config,appId);await assert.rejects(api.login(),/尚未开通/);assert.equal(api.session,'');
});
