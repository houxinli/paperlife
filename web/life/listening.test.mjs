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
  let p={id:'fiction-a',role:'owner'};const notices=[],busy=[],saved=[],audio=[];
  const ui=createListening({api,profile:()=>p,isGuest:()=>guest,canConfigure:()=>admin,canStart,note:s=>notices.push(s),onBusy:b=>busy.push(b),onSaved:s=>saved.push(s),fetchAudio:async p=>{audio.push(p);return 'blob:fiction';}});
  return {ui,roots,all,listeners,notices,busy,saved,audio,setProfile:v=>p=v,get:id=>document.getElementById(id),button:text=>all().find(n=>n.tagName==='button'&&n.textContent===text)};
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
test('consent and mutual exclusion prevent creation; pagehide stops capture and awaits saved',async()=>{
  const original={prepare:ListeningCall.prototype.prepare,connect:ListeningCall.prototype.connect,stop:ListeningCall.prototype.stop};let call,stops=0,posts=0;
  ListeningCall.prototype.prepare=async function(){call=this;};ListeningCall.prototype.connect=function(){this.ready=false;};ListeningCall.prototype.stop=function(){stops++;this.stopping=true;};
  try{
    const h=setup(async(path,method)=>{if(method==='POST'){posts++;return {id:'fiction-s',ticket:'fiction-ticket'};}return provider;});await h.ui.load();await h.get('listening-record-form').onsubmit({preventDefault(){}});assert.equal(posts,0);
    h.get('listening-consent').checked=true;await h.get('listening-record-form').onsubmit({preventDefault(){}});assert.equal(posts,1);assert.equal(h.ui.active,true);
    call.onEvent({type:'listening_transcript',segments:[{id:'x',speaker_id:'speaker_1',text:'虚构实时文字'}]});assert.match(h.roots[0].textContent,/暂定/);assert.match(h.roots[0].textContent,/虚构实时文字/);
    h.listeners.pagehide();assert.equal(stops,1);assert.equal(h.ui.active,true);call.onEvent({type:'saved',seconds:1});assert.equal(h.saved.length,0);assert.match(h.get('listening-state').textContent,/已收到/);call.onEvent({type:'saved',session:'fiction-s',recognition_status:'complete'});assert.equal(h.saved.length,1);call.onEvent({type:'ended'});assert.equal(h.ui.active,false);assert.deepEqual(h.busy,[true,false]);
    const blocked=setup(async()=>{posts++;return provider;},{canStart:()=>false});await blocked.ui.load();blocked.get('listening-consent').checked=true;const before=posts;await blocked.get('listening-record-form').onsubmit({preventDefault(){}});assert.equal(posts,before);
  }finally{Object.assign(ListeningCall.prototype,original);}
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
