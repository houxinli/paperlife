import test from 'node:test';
import assert from 'node:assert/strict';
import {createListening} from './listening.js';
import {ListeningCall} from './listening-audio.js';
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.attributes={};this._text='';}
  set textContent(v){this._text=String(v??'');this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this._text='';this.children=nodes;}
  setAttribute(k,v){this.attributes[k]=String(v);}pause(){}load(){}play(){this.played=true;return Promise.resolve();}scrollIntoView(){}
}
const detail=()=>({session:{id:'fiction-s',profile:'fiction-a',title:'虚构通话',version:3,status:'ended',audio_path:'/life/sessions/fiction-s/audio'},participants:[{id:'person-a',name:'虚构长辈',relationship:'家人',is_subject:true}],speakers:[{id:'speaker_1',label:'声音组 1',participant_id:'person-a'},{id:'speaker_2',label:'声音组 2',participant_id:null}],segments:[{id:'seg-a',speaker_id:'speaker_1',text:'校订文字',original_text:'原始文字',original_speaker_id:'speaker_1',start_ms:1200,end_ms:2300}],original_segments:[{id:'seg-a',speaker_id:'speaker_1',text:'原始文字',start_ms:1200,end_ms:2300}],recognition_status:'complete',can_edit:true,versions:[{version:2,created_at:'2026-01-01'}]});
function setup(api,{guest=false,admin=false,canStart=()=>true}={}){
  const roots=['listening-workspace','listening-review','listening-settings'].map(id=>Object.assign(new Element('section'),{id}));
  const all=()=>roots.flatMap(function walk(n){return [n,...n.children.flatMap(walk)];});
  const listeners={};globalThis.document={hidden:false,createElement:t=>new Element(t),getElementById:id=>all().find(n=>n.id===id),addEventListener:(n,f)=>listeners[n]=f};globalThis.window={addEventListener:(n,f)=>listeners[n]=f};
  let p={id:'fiction-a',name:'虚构外婆',role:'owner'};const notices=[],busy=[],saved=[],audio=[],opened=[];
  const ui=createListening({api,profile:()=>p,isGuest:()=>guest,canConfigure:()=>admin,canStart,note:s=>notices.push(s),onBusy:b=>busy.push(b),onSaved:s=>saved.push(s),onOpenBook:()=>opened.push(1),fetchAudio:async p=>{audio.push(p);return 'blob:fiction';}});
  return {ui,roots,all,listeners,notices,busy,saved,audio,opened,setProfile:v=>p=v,get:id=>document.getElementById(id),button:text=>all().find(n=>n.tagName==='button'&&n.textContent===text)};
}
const provider={enabled:true,configured:true,can_configure:true};
const baseApi=async path=>path.endsWith('/provider')?provider:detail();
test('disabled ASR shows setup needed and admin saves boolean without credentials',async()=>{
  const writes=[];const h=setup(async(path,method,body)=>{if(method==='PUT')writes.push(body);return {...provider,enabled:false};},{admin:true});await h.ui.load();
  assert.equal(h.get('listening-start').disabled,true);assert.match(h.roots[0].textContent,/尚未开通/);
  h.get('listening-enabled').checked=true;await h.get('listening-config-form').onsubmit({preventDefault(){}});assert.deepEqual(writes,[{enabled:true}]);
});
test('late review from old profile is discarded and guest stays readonly with scoped endpoint',async()=>{
  let resolve;const h=setup(()=>new Promise(r=>resolve=r));const pending=h.ui.openSession('fiction-s');h.setProfile({id:'fiction-b',role:'owner'});h.ui.clear();resolve(detail());await pending;assert.doesNotMatch(h.roots[1].textContent,/虚构通话/);
  const paths=[];const g=setup(async p=>{paths.push(p);return detail();},{guest:true});await g.ui.openSession('fiction-s');assert.deepEqual(paths,['/life/guest/listening/fiction-s']);assert.equal(g.get('listening-save'),undefined);assert.match(g.roots[1].textContent,/原始文字/);
});
test('review saves complete identity snapshot with merge, single segment reassignment and loaded revision',async()=>{
  const writes=[];const h=setup(async(path,method,body)=>{if(method==='PUT')writes.push(body);return detail();});await h.ui.openSession('fiction-s');
  h.get('listening-binding-1').value='person-a';h.get('listening-segment-speaker-0').value='speaker_2';h.get('listening-segment-text-0').value='虚构文字更正';await h.get('listening-edit-form').onsubmit({preventDefault(){}});
  assert.equal(writes[0].version,3);assert.deepEqual(writes[0].bindings,{speaker_1:'person-a',speaker_2:'person-a',unknown:null});assert.deepEqual(writes[0].overrides,{'seg-a':{speaker_id:'speaker_2',text:'虚构文字更正'}});assert.equal(writes[0].participants[0].is_subject,true);
});
test('conflict retains unsaved correction and original text, subject toggles are exclusive',async()=>{
  const d=detail();d.participants.push({id:'person-b',name:'虚构亲属',relationship:'家人',is_subject:false});const h=setup(async(path,method)=>{if(method==='PUT')throw Error('版本冲突409');return d;});await h.ui.openSession('fiction-s');
  h.get('listening-subject-1').checked=true;h.get('listening-subject-1').onchange();assert.equal(h.get('listening-subject-0').checked,false);
  h.get('listening-segment-text-0').value='不要丢失的更正';await h.get('listening-edit-form').onsubmit({preventDefault(){}});assert.equal(h.get('listening-segment-text-0').value,'不要丢失的更正');assert.match(h.roots[1].textContent,/版本冲突/);assert.match(h.roots[1].textContent,/原始文字/);
});
test('individual sentences can return to unknown and added participant ids stay unique',async()=>{
  const d=detail();d.participants[0].id='person-1';d.speakers[0].participant_id='person-1';const h=setup(async()=>d);await h.ui.openSession('fiction-s');
  const options=h.get('listening-segment-speaker-0').children.map(o=>o.value);assert.ok(options.includes('unknown'));
  await h.button('添加参与者').onclick();h.get('listening-name-1').value='虚构新亲属';
  assert.ok(h.get('listening-binding-0').children.some(o=>o.value!=='person-1'&&o.value!==''));
});
test('timestamp playback uses guest authorization path and clear discards pending audio',async()=>{
  const h=setup(baseApi,{guest:true});await h.ui.openSession('fiction-s');await h.button('回听 0:01 原声').onclick();assert.deepEqual(h.audio,['/life/guest/history/fiction-s/audio?track=user']);
  const player=h.get('listening-audio');await player.onloadedmetadata();assert.equal(player.currentTime,1.2);assert.equal(player.played,true);h.ui.clear();assert.equal(player.src,'');
});
test('ongoing recognition and readers cannot edit identity even when detail offers editing',async()=>{
  const d=detail();d.session.status='active';d.recognition_status='partial';const h=setup(async()=>d);await h.ui.openSession('fiction-s');assert.equal(h.get('listening-save'),undefined);
  const r=setup(baseApi);r.setProfile({id:'fiction-a',role:'reader'});await r.ui.openSession('fiction-s');assert.equal(r.get('listening-save'),undefined);
});
// 录音页、小结页用到的接口：会话、旁听详情（带两端建议）、整理进度。
const callDetail=(sid,{version=0,separated=true}={})=>({session:{id:sid,profile:'fiction-a',title:'和虚构外婆的通话',version,status:'ended'},participants:[{id:'subject',name:'虚构外婆',relationship:'',is_subject:true},{id:'me',name:'我',relationship:'',is_subject:false}],
  speakers:[],recognition_status:'complete',can_edit:true,versions:[],
  segments:[{id:'a',speaker_id:'3',text:'虚构：那年我们住在老街上，门口有棵槐树。',start_ms:0,end_ms:4000},{id:'b',speaker_id:'1',text:'虚构：后来呢？',start_ms:4000,end_ms:5000},{id:'c',speaker_id:'3',text:'虚构：后来去了纺织厂。',start_ms:5000,end_ms:8000},{id:'d',speaker_id:'1',text:'虚构：两个人一起说的话',start_ms:8000,end_ms:12000}],
  original_segments:[],suggestion:separated?{sides:{a:'far',b:'near',c:'far'},uncertain:['d'],separated:true,mode:'two_sides'}:{sides:{},uncertain:[],separated:false,mode:'unseparated'}});
function recorderApi(state){
  return async(path,method,body)=>{
    if(path.endsWith('/provider'))return provider;
    if(method==='POST'){state.posts.push(body);const next=state.responses.shift();if(next instanceof Error)throw next;return next||{id:`fiction-s${state.posts.length}`,ticket:`fiction-t${state.posts.length}`};}
    const sid=decodeURIComponent(path.split('/').pop());
    if(method==='PUT'){state.puts.push({sid,body});return callDetail(sid,{version:1});}
    if(path.endsWith('/processing'))return state.jobs;
    if(/\/profiles\/[^/]+\/sessions$/.test(path))return state.sessions;
    return state.details[sid]||callDetail(sid);
  };
}
const settle=async(n=6)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
async function withRecorder(responses,run,{wakeLock,guest=false,notes=true,details={},jobs=[]}={}){
  const original={prepare:ListeningCall.prototype.prepare,connect:ListeningCall.prototype.connect,stop:ListeningCall.prototype.stop,sendControl:ListeningCall.prototype.sendControl,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval,navigator:Object.getOwnPropertyDescriptor(globalThis,'navigator')};
  const timers=new Map();let timerId=0,call,prepares=0,stops=0;const tickets=[],controls=[];
  const state={responses,posts:[],puts:[],jobs,sessions:[],details};
  globalThis.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
  globalThis.setInterval=()=>0;globalThis.clearInterval=()=>{};
  const flush=async()=>{for(const [id,{fn}] of [...timers]){timers.delete(id);await fn();}};
  Object.defineProperty(globalThis,'navigator',{value:wakeLock?{wakeLock}:{},configurable:true});
  ListeningCall.prototype.prepare=async function(){prepares++;call=this;};
  ListeningCall.prototype.connect=function(ticket){tickets.push(ticket);this.ready=false;};
  ListeningCall.prototype.stop=function(){stops++;this.stopping=true;};
  ListeningCall.prototype.sendControl=function(data){controls.push(JSON.parse(JSON.stringify(data)));};
  try{
    const h=setup(recorderApi(state),{guest});
    await h.ui.load();h.get('listening-title').value='虚构标题';h.get('listening-consent').checked=true;
    if(h.get('listening-notes-enabled'))h.get('listening-notes-enabled').checked=notes;
    await h.get('listening-record-form').onsubmit({preventDefault(){}});
    await run({h,flush,timers,state,posts:state.posts,tickets,controls,call:()=>call,counts:()=>({prepares,stops})});
  }finally{
    Object.assign(ListeningCall.prototype,{prepare:original.prepare,connect:original.connect,stop:original.stop,sendControl:original.sendControl});
    Object.assign(globalThis,{setTimeout:original.setTimeout,clearTimeout:original.clearTimeout,setInterval:original.setInterval,clearInterval:original.clearInterval});
    if(original.navigator)Object.defineProperty(globalThis,'navigator',original.navigator);
  }
}
const httpError=(status,message='虚构错误')=>Object.assign(Error(message),{status});
const seg=(id,text,speaker='1')=>({id,speaker_id:speaker,text,start_ms:0,end_ms:1});
const bubbles=h=>h.all().filter(n=>String(n.className).startsWith('listening-bubble')).map(n=>n.textContent);
async function endRecording(call,sid='fiction-s1'){call().onEvent({type:'saved',session:sid,recognition_status:'complete'});call().onEvent({type:'ended',status:'ended',recognition_status:'complete'});await settle();}
test('home is one check card, one consent box and recent calls that say what to do next',async()=>{
  const state={responses:[],posts:[],puts:[],details:{},jobs:[{session:'old-2',status:'completed'},{session:'old-3',status:'running'}],
    sessions:[{id:'old-1',title:'和虚构外婆的通话',status:'ended',mode:'listening',version:0,created_at:'2026-11-07 05:35'},{id:'old-2',title:'虚构通话二',status:'ended',mode:'listening',version:1,created_at:'2026-11-06 05:35'},{id:'old-3',title:'虚构通话三',status:'interrupted',mode:'listening',version:1,created_at:'2026-11-05 05:35'},{id:'chat',title:'虚构聊天',status:'ended',mode:'dialogue',version:0,created_at:'2026-11-04 05:35'}]};
  const h=setup(recorderApi(state));await h.ui.load();await settle();
  const home=h.roots[0].textContent;
  assert.match(home,/记录和虚构外婆的通话/);assert.match(home,/打开外放/);assert.match(home,/放在扬声器旁边/);assert.match(home,/插上电源/);
  assert.match(home,/虚构外婆和我都知道这次通话会被录音和识别/);assert.equal(h.get('listening-start').textContent,'开始记录');
  const recent=h.get('listening-recent').textContent;
  assert.match(recent,/和虚构外婆的通话.*待确认说话人/);assert.match(recent,/虚构通话二.*已整理/);assert.match(recent,/虚构通话三.*整理中/);assert.doesNotMatch(recent,/虚构聊天/);
  // 开始前要勾选同意；标题和 AI 笔记开关都在“更多设置”里。
  await h.get('listening-record-form').onsubmit({preventDefault(){}});assert.match(h.get('listening-state').textContent,/请先确认/);assert.equal(state.posts.length,0);
  assert.equal(h.get('listening-notes-enabled').checked,true);
  await h.all().find(n=>n.className==='listen-call').onclick();await settle();
  assert.ok(h.get('listening-confirm'));assert.match(h.roots[0].textContent,/0:12 · 4 句/);
});
test('recording page shows the timer and labels captions by call side; unsure lines get a question mark',async()=>{
  await withRecorder([],async({h,call})=>{
    assert.ok(h.get('listening-timer'));assert.equal(h.get('listening-stop').textContent,'结束并保存');assert.match(h.get('listening-live').textContent,/“通话那头”默认是虚构外婆/);
    call().onEvent({type:'ready'});assert.match(h.get('listening-state').textContent,/正在记录/);
    call().onEvent({type:'listening_transcript',from:0,segments:[seg('s1','虚构：那年住在河边','3'),seg('s2','虚构：后来呢','1'),seg('s3','虚构：两人同时说','2')],total:3,sides:{s1:'far',s2:'near?'}});
    assert.deepEqual(bubbles(h),['虚构外婆虚构：那年住在河边','我？虚构：后来呢','声音组 2虚构：两人同时说']);
    assert.deepEqual(h.all().filter(n=>String(n.className).startsWith('listening-bubble')).map(n=>n.className),['listening-bubble side-far','listening-bubble side-near','listening-bubble']);
    // 分界自适应后早先的句子可能改边：只发变化的归边也会重绘整段。
    call().onEvent({type:'listening_transcript',from:3,segments:[],total:3,sides:{s2:'near',s3:'near'}});
    assert.deepEqual(bubbles(h),['虚构外婆虚构：那年住在河边','我虚构：后来呢','我虚构：两人同时说']);
  });
});
test('stopping opens the summary; one tap confirms both sides and progress leads to the drafts',async()=>{
  await withRecorder([],async({h,call,flush,state})=>{
    call().onEvent({type:'ready'});h.get('listening-stop').onclick();assert.match(h.get('listening-state').textContent,/正在保存/);
    await endRecording(call);
    assert.match(h.roots[0].textContent,/通话已保存/);assert.match(h.get('listening-state').textContent,/录音和识别都已保存/);
    assert.equal(h.get('listening-side-far').value,'subject');assert.equal(h.get('listening-side-near').value,'me');
    const text=h.get('listening-confirm').textContent;
    // 每边附最长的原话，没听出的句子单独说明，不会算到任何人头上。
    assert.match(text,/通话那头是.*那年我们住在老街上/);assert.match(text,/手机旁边是.*后来呢/);assert.match(text,/另有 1 句没听出是谁/);
    await h.get('listening-confirm-save').onclick();await settle();
    assert.deepEqual(state.puts,[{sid:'fiction-s1',body:{version:0,participants:callDetail('x').participants,bindings:{far:'subject',near:'me'},overrides:{a:{speaker_id:'far'},b:{speaker_id:'near'},c:{speaker_id:'far'}}}}]);
    assert.match(h.get('listening-progress').textContent,/AI 正在整理/);assert.equal(h.get('listening-open-book').hidden,true);
    state.jobs.push({session:'fiction-s1',status:'completed'});await flush();await settle();
    assert.match(h.get('listening-progress').textContent,/整理完成/);assert.equal(h.get('listening-open-book').hidden,false);
    h.get('listening-open-book').onclick();assert.equal(h.opened.length,1);
  });
});
test('a different person can be named for a side before confirming',async()=>{
  await withRecorder([],async({h,call,state})=>{
    call().onEvent({type:'ready'});h.get('listening-stop').onclick();await endRecording(call);
    const near=h.get('listening-side-near');near.value='__new';near.onchange();assert.equal(near.value,'me');assert.equal(h.get('listening-side-name-near').hidden,false);
    h.get('listening-side-name-near').value='虚构妹妹';await h.all().filter(n=>n.tagName==='button'&&n.textContent==='添加')[1].onclick();
    assert.notEqual(near.value,'me');assert.ok(h.get('listening-side-far').children.some(o=>o.textContent==='虚构妹妹'));
    await h.get('listening-confirm-save').onclick();await settle();
    const body=state.puts[0].body;assert.equal(body.participants.find(p=>p.id===body.bindings.near).name,'虚构妹妹');
  });
});
test('when the two sides cannot be told apart the summary sends you to line-by-line review',async()=>{
  await withRecorder([],async({h,call})=>{
    call().onEvent({type:'ready'});h.get('listening-stop').onclick();await endRecording(call);
    assert.match(h.get('listening-confirm').textContent,/没能自动分出通话两端/);assert.equal(h.get('listening-confirm-save'),undefined);
    await h.button('逐句检查').onclick();await settle();assert.match(h.roots[1].textContent,/逐句核对与回听/);
  },{details:{'fiction-s1':callDetail('fiction-s1',{separated:false})}});
});
test('limit and dropped connections continue as numbered parts on the same microphone',async()=>{
  await withRecorder([],async({h,flush,posts,tickets,call,counts})=>{
    assert.deepEqual(tickets,['fiction-t1']);assert.equal(posts[0].title,'虚构标题');
    call().onEvent({type:'ready'});assert.match(h.get('listening-state').textContent,/正在记录/);
    call().onEvent({type:'saved',seconds:7200});call().onEvent({type:'ending',reason:'limit'});assert.match(h.get('listening-state').textContent,/第1段已满两小时/);
    call().onEvent({type:'saved',session:'fiction-s1',recognition_status:'complete'});call().onEvent({type:'ended',status:'ended',recognition_status:'complete'});
    assert.equal(h.ui.active,true);await flush();
    assert.deepEqual(tickets,['fiction-t1','fiction-t2']);assert.equal(posts[1].title,'虚构标题（第2段）');assert.deepEqual(posts[1].participants,posts[0].participants);
    call().onEvent({type:'ready'});call().onEvent({type:'saved',seconds:5});assert.match(h.get('listening-state').textContent,/正在记录 · 第2段/);
    call().onEvent({type:'disconnected'});assert.match(h.get('listening-state').textContent,/正在重新连接/);await flush();
    assert.equal(tickets.at(-1),'fiction-t3');assert.equal(posts[2].title,'虚构标题（第3段）');assert.equal(counts().prepares,1);assert.deepEqual(h.saved,['fiction-s1']);
    // 名单里补上了档案本人和“我”，两段之间保持一致。
    assert.deepEqual(posts[0].participants.map(p=>[p.id,p.name,p.is_subject]),[['subject','虚构外婆',true],['me','我',false]]);
  });
});
test('a part that is still finishing (409) is retried; fatal errors and repeated failures stop recording',async()=>{
  await withRecorder([undefined,httpError(409,'已有聊天正在连接或进行中'),undefined],async({h,flush,tickets,call})=>{
    call().onEvent({type:'ready'});call().onEvent({type:'disconnected'});await flush();assert.equal(tickets.length,1);
    assert.match(h.get('listening-state').textContent,/正在重新连接/);await flush();assert.deepEqual(tickets,['fiction-t1','fiction-t3']);
    // 第一段只撑了一瞬间就断开（记 1 次），之后再有两段没开始识别就停止。
    for(let i=0;i<2;i++){assert.equal(h.ui.active,true);call().onEvent({type:'error',message:'虚构识别失败'});call().onEvent({type:'ended',status:'interrupted'});await flush();}
    assert.equal(h.ui.active,false);assert.match(h.get('listening-state').textContent,/多次尝试都没能接上/);await settle();
  });
  await withRecorder([undefined,httpError(403,'无权限')],async({h,flush,posts,call})=>{
    call().onEvent({type:'ready'});call().onEvent({type:'disconnected'});await flush();
    assert.equal(posts.length,2);assert.equal(h.ui.active,false);assert.match(h.get('listening-state').textContent,/无权限/);await flush();assert.equal(posts.length,2);await settle();
  });
});
test('first part failure reports instead of retrying; user stop and hidden page never continue',async()=>{
  await withRecorder([],async({h,flush,posts,call})=>{
    call().onEvent({type:'error',message:'虚构配置错误'});call().onEvent({type:'ended',status:'interrupted'});await flush();
    assert.equal(posts.length,1);assert.equal(h.ui.active,false);assert.match(h.get('listening-state').textContent,/没能开始记录/);assert.ok(h.get('listening-start'));
  });
  await withRecorder([],async({h,flush,posts,call,counts})=>{
    call().onEvent({type:'ready'});h.listeners.visibilitychange();assert.equal(counts().stops,0);
    document.hidden=true;h.listeners.visibilitychange();assert.equal(counts().stops,1);
    await endRecording(call);await flush();
    assert.equal(posts.length,1);assert.equal(h.ui.active,false);assert.match(h.get('listening-state').textContent,/录音和识别都已保存/);
  });
});
test('screen stays awake while recording, is released afterwards, and unsupported devices get a hint',async()=>{
  const requests=[];let released=0;
  const wakeLock={request:async kind=>{requests.push(kind);return {addEventListener(){},release:async()=>{released++;}};}};
  await withRecorder([],async({h,call})=>{
    await Promise.resolve();assert.deepEqual(requests,['screen']);assert.equal(h.get('listening-hint').textContent,'');
    call().onEvent({type:'ready'});h.get('listening-stop').onclick();await endRecording(call);
    assert.equal(released,1);
  },{wakeLock});
  await withRecorder([],async({h})=>{assert.match(h.get('listening-hint').textContent,/自动锁屏/);});
});
test('capture stopped by the system between parts finishes instead of reconnecting',async()=>{
  await withRecorder([],async({h,flush,posts,call})=>{
    call().onEvent({type:'ready'});call().onEvent({type:'disconnected'});call().stopping=true;await flush();
    assert.equal(posts.length,1);assert.equal(h.ui.active,false);assert.match(h.get('listening-state').textContent,/录音已停止/);await settle();
  });
});
test('a flaky network that keeps dropping short parts stops instead of producing endless fragments',async()=>{
  const realNow=Date.now;let now=1000000;Date.now=()=>now;
  try{
    await withRecorder([],async({h,flush,posts,call})=>{
      call().onEvent({type:'ready'});now+=2*3600*1000;call().onEvent({type:'disconnected'});await flush();
      for(let i=0;i<2;i++){call().onEvent({type:'ready'});now+=60000;call().onEvent({type:'disconnected'});await flush();}
      assert.equal(h.ui.active,true);assert.equal(posts.length,4);
      for(let i=0;i<3;i++){call().onEvent({type:'ready'});now+=5000;call().onEvent({type:'disconnected'});await flush();}
      assert.equal(h.ui.active,false);assert.equal(posts.length,6);assert.match(h.get('listening-state').textContent,/多次尝试都没能接上/);await settle();
    });
  }finally{Date.now=realNow;}
});
test('live transcript keeps every sentence across parts and applies tail revisions in place',async()=>{
  await withRecorder([],async({h,flush,call})=>{
    call().onEvent({type:'ready'});
    call().onEvent({type:'listening_transcript',from:0,segments:[seg('s1','虚构第一句')],total:1});
    call().onEvent({type:'listening_transcript',from:1,segments:[seg('s2','虚构第二','2')],total:2});
    call().onEvent({type:'listening_transcript',from:1,segments:[seg('s2','虚构第二句修订','2')],total:2});
    call().onEvent({type:'listening_transcript',from:2,segments:[seg('s3','虚构第三句')],total:3});
    assert.deepEqual(bubbles(h),['声音组 1虚构第一句','声音组 2虚构第二句修订','声音组 1虚构第三句']);
    call().onEvent({type:'listening_transcript',from:1,segments:[seg('s2','虚构第二句和第三句合并','2')],total:2});
    assert.deepEqual(bubbles(h),['声音组 1虚构第一句','声音组 2虚构第二句和第三句合并']);
    call().onEvent({type:'ending',reason:'limit'});call().onEvent({type:'saved',session:'fiction-s1',recognition_status:'complete'});call().onEvent({type:'ended',status:'ended',recognition_status:'complete'});await flush();
    call().onEvent({type:'ready'});call().onEvent({type:'listening_transcript',from:0,segments:[seg('s1','虚构下一段第一句','unknown')],total:1,sides:{s1:'far'}});
    assert.deepEqual(bubbles(h),['声音组 1虚构第一句','声音组 2虚构第二句和第三句合并','虚构外婆虚构下一段第一句']);
    assert.match(h.get('listening-live').textContent,/第2段/);
  });
});
test('AI notes put follow-up questions first, add points and show failures without stopping',async()=>{
  await withRecorder([],async({h,call,controls})=>{
    call().onEvent({type:'ready'});assert.deepEqual(controls,[{type:'notes',enabled:true}]);
    assert.equal(h.get('listening-notes').hidden,false);assert.match(h.get('listening-notes').textContent,/大约每分钟/);
    call().onEvent({type:'listening_notes',points:[{text:'那年住在河边',speaker:'虚构外婆'}],questions:[{text:'河边的房子什么样？',why:'刚提到'}]});
    call().onEvent({type:'listening_notes',points:[{text:'有一座木桥'}],questions:[{text:'桥是谁修的？'}]});
    const text=h.get('listening-notes').textContent;
    assert.match(text,/可以追问.*桥是谁修的？.*要点.*虚构外婆：那年住在河边.*有一座木桥/);assert.doesNotMatch(text,/河边的房子什么样/);
    call().onEvent({type:'listening_notes',error:'AI 笔记这次没有生成，稍后会再试。'});
    assert.match(h.get('listening-notes').textContent,/这次没有生成/);assert.match(h.get('listening-notes').textContent,/有一座木桥/);assert.equal(h.ui.active,true);
  });
  await withRecorder([],async({h,call,controls})=>{
    call().onEvent({type:'ready'});assert.deepEqual(controls,[]);assert.equal(h.get('listening-notes').hidden,true);
  },{notes:false});
});
test('narrator links record without notes or the confirmation step',async()=>{
  await withRecorder([],async({h,call,controls,posts})=>{
    assert.equal(h.get('listening-notes-enabled'),undefined);assert.deepEqual(posts[0].participants,[]);
    call().onEvent({type:'ready'});assert.deepEqual(controls,[]);
    h.get('listening-stop').onclick();await endRecording(call);
    assert.equal(h.get('listening-confirm'),undefined);assert.ok(h.get('listening-start'));assert.match(h.get('listening-state').textContent,/录音和识别都已保存/);
  },{guest:true});
});
