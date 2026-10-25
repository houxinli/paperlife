import test from 'node:test';
import assert from 'node:assert/strict';
import {createChapters} from './chapters.js';

// Small DOM boundary for the real controller; fixtures are entirely fictional.
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.hidden=false;this.disabled=false;this.value='';this.attributes={};this._text='';}
  set textContent(value){this._text=String(value??'');this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this._text='';this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=String(value);}
}
function setup(api,{role='owner',admin=false}={}){
  const roots=['chapter-workspace','editor-model-settings'].map(()=>new Element('section'));
  roots[0].id='chapter-workspace';roots[1].id='editor-model-settings';
  const all=()=>roots.flatMap(function walk(n){return [n,...n.children.flatMap(walk)];});
  globalThis.document={createElement:tag=>new Element(tag),getElementById:id=>all().find(n=>n.id===id)};
  let p={id:'fiction-a',name:'虚构人物甲',role};const notices=[],audio=[];
  const ui=createChapters({api,profile:()=>p,note:s=>notices.push(s),canConfigure:()=>admin,openSession:(...args)=>audio.push(args)});
  return {ui,roots,all,notices,audio,setProfile:next=>{p=next;},get:id=>document.getElementById(id),button:text=>all().find(n=>n.tagName==='button'&&n.textContent===text)};
}
const material={kind:'session',id:'fiction-session',title:'虚构少年往事',revision:2,chars:20,has_audio:true};
const draft={id:'draft-2',number:2,status:'draft',edit_version:3,title:'少年',body:'虚构草稿',paragraphs:[],sources:[],evidence:[],stale:false};
const accepted={id:'confirmed-1',number:1,status:'confirmed',edit_version:1,title:'已确认往事',body:'虚构已确认内容',sources:[],evidence:[]};
function fixtureApi(overrides={}){
  return async(path,method='GET',data)=>{
    if(overrides[path])return overrides[path](method,data);
    if(path.endsWith('/materials'))return {materials:[material,{kind:'source',id:'fiction-import',title:'虚构导入',revision:1,chars:4,has_audio:false}]};
    if(path.endsWith('/writing-jobs'))return {jobs:[]};
    if(path.endsWith('/chapters'))return {chapters:[{id:'chapter-a',title:'虚构章节',published_version_id:'confirmed-1',published_version:1,latest_version:2,draft_count:1,stale:false}],jobs:[]};
    if(path==='/life/chapters/chapter-a')return {chapter:{id:'chapter-a',published_version_id:'confirmed-1'},versions:[draft,accepted]};
    throw Error('Unexpected path '+path);
  };
}
test('profile switch discards late list results and clear removes sensitive content',async()=>{
  let resolve;const old=new Promise(r=>{resolve=r;});
  const h=setup(fixtureApi({'/life/profiles/fiction-a/chapters':()=>old}));
  const loading=h.ui.load();h.setProfile({id:'fiction-b',role:'reader'});h.ui.clear();
  resolve({chapters:[{id:'secret',title:'不应显示的旧档案'}],jobs:[]});await loading;
  assert.doesNotMatch(h.roots[0].textContent,/不应显示的旧档案/);
  assert.equal(h.get('chapter-detail').textContent,'');
});
test('reader never fetches material or model settings and only shows confirmed versions',async()=>{
  const paths=[];const h=setup(async(...args)=>{paths.push(args[0]);return fixtureApi()(...args);},{role:'reader'});
  await h.ui.load();await h.button('虚构章节 · 已确认第 1 版').onclick();
  assert.equal(paths.some(p=>p.includes('/materials')||p.includes('/editor-models')),false);
  assert.doesNotMatch(h.get('chapter-detail').textContent,/虚构草稿/);
  assert.match(h.get('chapter-detail').textContent,/虚构已确认内容/);
});
test('multiple selected material identities generate a chapter without confirming it',async()=>{
  const writes=[];const h=setup(fixtureApi({
    '/life/profiles/fiction-a/chapters':async(method,data)=>{if(method==='POST'){writes.push([method,data]);return {id:'new-chapter'};}return {chapters:[],jobs:[]};},
    '/life/chapters/new-chapter/generate':async(method,data)=>{writes.push([method,data]);return {id:'new-job',status:'pending'};},
    '/life/chapters/new-chapter':async()=>({chapter:{id:'new-chapter'},versions:[]})
  }));
  await h.ui.load();for(const n of h.all().filter(n=>n.type==='checkbox'))n.checked=true;
  h.get('chapter-new-title').value='虚构新章';h.get('chapter-new-brief').value='写少年往事';
  await h.get('chapter-create-form').onsubmit({preventDefault(){}});
  assert.deepEqual(writes,[['POST',{title:'虚构新章',brief:'写少年往事'}],['POST',{sources:[{kind:'session',id:'fiction-session'},{kind:'source',id:'fiction-import'}],brief:'写少年往事'}]]);
});
test('draft edits save with edit_version before explicit confirmation; confirmed text stays visible',async()=>{
  let version={...draft};const calls=[];const h=setup(fixtureApi({
    '/life/chapters/chapter-a':async()=>({chapter:{id:'chapter-a',published_version_id:'confirmed-1'},versions:[version,accepted]}),
    '/life/chapters/chapter-a/versions/draft-2':async(method,data)=>{calls.push([method,data]);version={...version,...data,edit_version:4};return version;},
    '/life/chapters/chapter-a/versions/draft-2/confirm':async(method,data)=>{calls.push([method,data]);version={...version,status:'confirmed'};return version;}
  }));
  await h.ui.load();await h.button('虚构章节 · 已确认第 1 版 · 草稿 1 份').onclick();
  assert.match(h.get('chapter-detail').textContent,/虚构已确认内容/);
  h.get('chapter-draft-body').value='虚构修改';h.get('chapter-draft-body').oninput();
  assert.equal(h.get('chapter-confirm').disabled,true);
  await h.get('chapter-edit-form').onsubmit({preventDefault(){}});
  assert.deepEqual(calls,[['PUT',{title:'少年',body:'虚构修改',edit_version:3}]]);
  await h.get('chapter-confirm').onclick();assert.deepEqual(calls[1],['POST',{edit_version:4}]);
});
test('stale draft blocks confirmation and evidence distinguishes corrected text and imports',async()=>{
  const corrected={unit_id:'session:fiction-session:2',source_kind:'session',source_id:'fiction-session',revision:2,title:'虚构校订',text:'虚构校订快照',seconds:null,turn_id:null};
  const imported={unit_id:'source:fiction-import:1',source_kind:'source',source_id:'fiction-import',revision:1,title:'虚构导入',text:'虚构原文快照'};
  const h=setup(fixtureApi({'/life/chapters/chapter-a':async()=>({chapter:{id:'chapter-a'},versions:[{...draft,stale:true,sources:[corrected,imported],evidence:[{...corrected,quote:'虚构校订'},{...imported,quote:'虚构原文'}]},accepted]})}));
  await h.ui.load();await h.button('虚构章节 · 已确认第 1 版 · 草稿 1 份').onclick();
  assert.equal(h.get('chapter-confirm').disabled,true);assert.match(h.get('chapter-detail').textContent,/虚构校订快照/);
  assert.match(h.get('chapter-detail').textContent,/无法逐句定位/);assert.match(h.get('chapter-detail').textContent,/导入资料，无录音/);
  const audio=h.all().filter(n=>n.tagName==='button'&&n.textContent.includes('回听'));
  assert.equal(audio.length,1);await audio[0].onclick();assert.deepEqual(h.audio,[['fiction-session',0]]);
  assert.ok(h.button('用当前素材重新生成草稿'));
});
test('material correction sends loaded revision and surfaces conflict without closing editor',async()=>{
  const writes=[];const h=setup(fixtureApi({'/life/profiles/fiction-a/materials/session/fiction-session':async(method,data)=>{if(method==='PUT'){writes.push(data);throw Error('素材版本已变化，请刷新后重试');}return {...material,text:'虚构校订',original_text:'虚构原始转写'};}}));
  await h.ui.load();await h.button('校订：虚构少年往事').onclick();h.get('chapter-material-text').value='虚构更正';
  await h.get('chapter-material-form').onsubmit({preventDefault(){}});
  assert.deepEqual(writes,[{text:'虚构更正',version:2}]);assert.match(h.get('chapter-material-editor').textContent,/素材版本已变化/);
});
test('admin models save two roles without persisting or submitting blank credentials',async()=>{
  const writes=[];const h=setup(fixtureApi({'/life/editor-models':async(method,data)=>{if(method==='PUT')writes.push(data);return {can_configure:true,extractor:{provider:'doubao_realtime',model:'1.2.1.1',configured:true},writer:{provider:'deepseek',model:'deepseek-chat',configured:false}};}}),{admin:true});
  await h.ui.load();assert.equal(h.roots[1].hidden,false);
  h.get('writer-api-key').value='fictional-test-key';await h.get('editor-model-form').onsubmit({preventDefault(){}});
  assert.deepEqual(writes,[{extractor:{provider:'doubao_realtime',model:'1.2.1.1'},writer:{provider:'deepseek',model:'deepseek-chat',api_key:'fictional-test-key'}}]);
  assert.equal(h.get('writer-api-key').value,'');h.ui.clear();assert.equal(h.roots[1].hidden,true);
});
test('late detail response cannot replace a different selected chapter',async()=>{
  let resolve;const slow=new Promise(r=>{resolve=r;});
  const h=setup(fixtureApi({
    '/life/profiles/fiction-a/chapters':async()=>({chapters:[{id:'chapter-a',title:'虚构慢章'},{id:'chapter-b',title:'虚构快章'}]}),
    '/life/chapters/chapter-a':()=>slow,
    '/life/chapters/chapter-b':async()=>({chapter:{id:'chapter-b'},versions:[{...draft,id:'draft-b',title:'应保留的新章',body:'快章内容'}]})
  }));
  await h.ui.load();const first=h.button('虚构慢章 · 尚未确认 · 草稿 0 份').onclick();
  await h.button('虚构快章 · 尚未确认 · 草稿 0 份').onclick();
  resolve({chapter:{id:'chapter-a'},versions:[{...draft,title:'不能覆盖的新章',body:'旧请求内容'}]});await first;
  assert.equal(h.get('chapter-draft-title').value,'应保留的新章');
  assert.equal(h.get('chapter-draft-body').value,'快章内容');
});
test('save conflict remains visible and blocks confirmation until refreshed',async()=>{
  const h=setup(fixtureApi({'/life/chapters/chapter-a/versions/draft-2':async()=>{throw Error('章节已被其他整理者修改');}}));
  await h.ui.load();await h.button('虚构章节 · 已确认第 1 版 · 草稿 1 份').onclick();
  h.get('chapter-draft-body').value='虚构并发修改';h.get('chapter-draft-body').oninput();
  await h.get('chapter-edit-form').onsubmit({preventDefault(){}});
  assert.match(h.get('chapter-detail').textContent,/章节已被其他整理者修改/);
  assert.equal(h.get('chapter-confirm').disabled,true);
});
test('old detail action and errors cannot open or notify against a new profile',async()=>{
  let reject;const delayed=new Promise((_,r)=>{reject=r;});
  const h=setup(fixtureApi({'/life/chapters/chapter-a':()=>delayed}));await h.ui.load();
  const oldButton=h.button('虚构章节 · 已确认第 1 版 · 草稿 1 份');const pending=oldButton.onclick();
  h.setProfile({id:'fiction-b',role:'owner'});h.ui.clear();reject(Error('旧档案的错误信息'));await pending;
  assert.deepEqual(h.notices,[]);assert.equal(h.get('chapter-detail').textContent,'');
  await oldButton.onclick();assert.equal(h.get('chapter-detail').textContent,'');
});
