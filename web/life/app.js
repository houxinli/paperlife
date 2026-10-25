import {VoiceCall} from './audio.js';
import {createNarrator} from './narrator.js';
import {createBook} from './book.js';
import {createChapters} from './chapters.js';
import {createListening} from './listening.js';
import {resolveNarratorEntry} from './entry.js';
const BASE=location.pathname.split('/life')[0];
const $=id=>document.getElementById(id);
let user,profile,provider,call,editing,audioUrl;
let selectionEpoch=0,conversationProfile=null;
const liveRows={};
const params=new URLSearchParams(location.search);
const guestMode=params.get('view')==='guest';
let guestToken='';
if(guestMode){
  let session,local;try{session=window.sessionStorage;local=window.localStorage;}catch{}
  const entry=resolveNarratorEntry(location.hash,session,local);guestToken=entry.token;
  if(entry.hash)window.history.replaceState(null,'',location.pathname+location.search+entry.hash);
}
let wantedProfile=params.get('profile');
let view=params.get('view')==='read'?'read':params.get('view')==='book'?'book':'talk';
document.body.classList.toggle('simple',guestMode||view!=='read');
let token='';try{token=localStorage.getItem('paperlife_session_token')||'';}catch{}
const note=text=>{$('notice').textContent=text;};
async function api(path,method='GET',body){
  const response=await fetch(BASE+'/api'+path,{method,credentials:'same-origin',headers:{...(guestMode?{'X-Life-Link':guestToken}:{}),...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:`请求失败（${response.status}）`);}
  return response.json();
}
const action=fn=>async event=>{event?.preventDefault();try{await fn(event);}catch(e){note(e.message);}};
function button(text,fn,cls='secondary'){const b=document.createElement('button');b.textContent=text;b.className=cls;b.onclick=action(fn);return b;}
function busy(value){
  $('listening-workspace').inert=value;
  $('start').hidden=value;$('stop').hidden=!value;$('silence').hidden=!value;$('orb').classList.toggle('active',value);
  document.body.classList.toggle('call-active',value);$('family-view').disabled=value;$('interview-preferences').hidden=value;
  if(!value)$('resume-audio').hidden=true;
  for(const id of ['profiles','new-profile','topic','save-profile','talk-view','read-view'])$(id).disabled=value;
  document.querySelectorAll('[data-topic]').forEach(b=>b.disabled=value);
}
async function refresh(){
  if(guestMode){await refreshGuest();return;}
  user=await api('/auth/me');provider=await api('/life/provider');
  $('login-panel').hidden=true;$('workspace').hidden=false;document.body.classList.add('signed-in');
  $('identity').replaceChildren(document.createTextNode(`${user.display_name||user.username} · ID ${user.id} `),button('退出',logout));
  $('provider-panel').hidden=!provider.can_configure;
  $('provider-details').open=!provider.configured;
  const profiles=await api('/life/profiles');$('profiles').replaceChildren();
  for(const p of profiles){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;$('profiles').append(o);}
  const selected=profile?.id||wantedProfile;
  if(selected&&!profiles.some(p=>p.id===selected)){throw Error('这个人生档案尚未授权给当前账号。请联系档案所有者。');}
  if(selected)$('profiles').value=selected;
  if(profiles.length)await selectProfile();else{setView('read');$('profile-work').hidden=true;note('先新建一份人生档案，就可以添加聊天背景。');}
}
async function selectProfile(){
  listening.clear();
  chapters.clear();
  const epoch=++selectionEpoch;const selected=$('profiles').value;
  $('profile-work').hidden=true;$('profiles').disabled=true;
  try{
  const next=await api('/life/profiles/'+selected);if(epoch!==selectionEpoch)return;if(conversationProfile!==next.id){conversationProfile=next.id;$('transcript').replaceChildren();delete liveRows.user;delete liveRows.assistant;narrator.reset();$('live-caption').textContent='我们从一件小事聊起。';}profile=next;
  $('profile-name').value=profile.name;$('context').value=profile.context;$('voice-dialect').value=profile.voice_dialect||'sichuan';
  $('members-panel').hidden=profile.role!=='owner';$('links-panel').hidden=profile.role!=='owner';$('generated-link-label').hidden=true;
  $('topic').value='';
  const url=new URL(location.href);url.searchParams.set('profile',profile.id);url.searchParams.set('view',view);window.history.replaceState(null,'',url);
  setView(view);
  for(const id of ['profile-name','context','save-profile','voice-dialect'])$(id).disabled=profile.role==='reader';
  $('start').disabled=!provider.configured||profile.role==='reader';
  note(!provider.configured?'实时语音尚未配置。管理员在下方填写豆包凭据后即可开始。':'');
  await history();await book.load();await chapters.load();await listening.load();await loadPreferences();if(profile.role==='owner'){await members();await narratorLinks();}
  if(epoch===selectionEpoch)$('profile-work').hidden=false;
  }finally{if(epoch===selectionEpoch)$('profiles').disabled=false;}
}
async function history(){
  if(guestMode)return;
  const id=profile.id;const sessions=await api(`/life/profiles/${id}/sessions`);if(profile.id!==id)return;$('sessions').replaceChildren();
  if(!sessions.length)$('sessions').textContent='第一段故事，等您开口。';
  const statuses={connecting:'未连接',active:'进行中',ended:'已结束',interrupted:'连接中断'};
  for(const s of sessions){const b=button('',()=>openEditor(s.id),'story');const title=document.createElement('span');title.textContent=s.title;const small=document.createElement('small');small.textContent=`${s.created_at} UTC · ${statuses[s.status]||s.status}${s.version?' · 已校对':''}`;b.append(title,small);$('sessions').append(b);}
}
async function members(){
  const id=profile.id;const data=await api(`/life/profiles/${id}/members`);if(profile.id!==id)return;$('members').replaceChildren();
  for(const m of data){const row=document.createElement('div');row.textContent=`ID ${m.user} · ${{owner:'所有者',reader:'阅读者',editor:'整理者'}[m.role]}`;if(m.role!=='owner')row.append(button('撤销',async()=>{await api(`/life/profiles/${profile.id}/members/${m.user}`,'DELETE');await members();}));$('members').append(row);}
}
$('login-form').onsubmit=action(async()=>{const r=await api('/auth/login','POST',{username:$('username').value,password:$('password').value});token=r.session_id;try{localStorage.setItem('paperlife_session_token',token);}catch{}$('password').value='';note('');await refresh();});
async function logout(){listening.clear();call?.dispose();call=null;busy(false);await api('/auth/logout','POST');token='';try{localStorage.removeItem('paperlife_session_token');}catch{}location.reload();}
$('profiles').onchange=action(selectProfile);
$('new-profile').onclick=action(async()=>{const name=prompt('这是谁的人生档案？例如：外婆、姑婆');if(!name?.trim())return;profile=await api('/life/profiles','POST',{name:name.trim(),context:''});await refresh();});
$('profile-form').onsubmit=action(async()=>{profile=await api(`/life/profiles/${profile.id}`,'PUT',{name:$('profile-name').value,context:$('context').value,voice_dialect:$('voice-dialect').value});note('聊天背景已保存。下次开始聊天时生效。');});
$('member-form').onsubmit=action(async()=>{await api(`/life/profiles/${profile.id}/members/${Number($('member-id').value)}`,'PUT',{role:$('member-role').value});await members();note('授权已更新。');});
$('provider-form').onsubmit=action(async()=>{await api('/life/provider','PUT',{app_id:$('app-id').value,access_token:$('access-token').value,api_key:$('api-key').value});$('access-token').value='';$('api-key').value='';await refresh();note('凭据已保存在服务器。点击开始聊聊进行实际连接验证。');});
document.querySelectorAll('[data-topic]').forEach(b=>b.onclick=()=>{$('topic').value=b.dataset.topic;});
function onEvent(e){
  if(e.type==='ready'){$('call-state').textContent='已经接通';$('live-caption').textContent='我在这里，您慢慢说。';}
  if(e.type==='audio_state'){$('resume-audio').hidden=e.state!=='blocked';if(e.state==='blocked'){$('call-state').textContent='声音暂停了，点一下就能继续';}else if(e.state==='playing')$('call-state').textContent='拾光在说话';else if(e.state==='ready'&&call?.ready&&!call?.stopping)$('call-state').textContent='我在听，您慢慢讲';}
  if(e.type==='audio_start')$('call-state').textContent='拾光在说话';
  if(e.type==='preference_saved'){$('saved-state').textContent='记住您的聊天习惯了，以后也会留意。';refreshNames().catch(()=>{});}
  if(e.type==='ending'){$('call-state').textContent='听到了，我们先聊到这里。正在保存…';$('stop').disabled=true;}
  if(e.type==='saved')$('saved-state').textContent='正在记录，结束后会自动保存。';
  if(e.type==='error'||e.type==='notice')note(e.message);
  if(e.type==='transcript'){
    if(!e.text)return;
    const follow=$('transcript').scrollHeight-$('transcript').scrollTop-$('transcript').clientHeight<80;
    if(e.role==='assistant'&&e.text)$('live-caption').textContent=e.text;
    let row=liveRows[e.role];if(!row){row=document.createElement('div');row.className='turn '+e.role;const label=document.createElement('b');label.textContent=e.role==='user'?(profile.narrator_name||profile.name):(profile.interviewer_name||'拾光')+' · AI';row.append(label,document.createElement('p'));$('transcript').append(row);liveRows[e.role]=row;}
    row.lastChild.textContent=e.text;if(e.final)delete liveRows[e.role];if(follow)$('transcript').scrollTop=$('transcript').scrollHeight;
  }
  if(e.type==='interrupt'){$('call-state').textContent='我在听，您慢慢讲';delete liveRows.assistant;}
  if(e.type==='ended'||e.type==='disconnected'){call=null;busy(false);$('start').textContent='再聊一会儿';const saved=e.type==='ended'&&e.status==='ended';$('call-state').textContent=saved?'这次聊天已保存':'连接断开了';$('live-caption').textContent=saved?'谢谢您，下次我们接着聊。':'点下面的按钮，重新接通。';$('saved-state').textContent=saved?'原声和文字已留存，家人可以之后整理。':'已收到的内容会保留。';history().catch(e=>note(e.message));loadPreferences().catch(e=>note(e.message));if(!guestMode)book.load().catch(e=>note(e.message));narrator.history().catch(e=>note(e.message));if(view==='book')narrator.book().catch(e=>note(e.message));$('stop').disabled=false;}
}
$('start').onclick=action(async()=>{
  if(listening.active)throw Error('请先结束旁听录音，再开始 AI 对话。');
  document.querySelectorAll('audio').forEach(audio=>audio.pause());
  if(call)return;setView('talk');note('');busy(true);$('transcript').replaceChildren();delete liveRows.user;delete liveRows.assistant;
  $('call-state').textContent='正在打开麦克风…';$('live-caption').textContent='如果手机询问，请点“允许使用麦克风”。';
  const current=new VoiceCall(onEvent);call=current;
  try{
    await current.prepare();if(current.closed)return;
    const s=await api(guestMode?'/life/guest/sessions':`/life/profiles/${profile.id}/sessions`,'POST',($('topic').value.trim()?{topic:$('topic').value.trim()}:{}));
    if(current.closed)return;
    $('call-state').textContent='正在接通聊天伙伴…';current.connect(s.ticket);
  }catch(e){current.dispose();call=null;busy(false);$('call-state').textContent='暂时没有连接';throw e;}
});
$('stop').onclick=()=>{call?.stop();$('call-state').textContent='正在结束并保存…';if(call?.closed){call=null;busy(false);}};
$('silence').onclick=()=>call?.stopAnswer();
$('resume-audio').onclick=action(async()=>{await call?.resumeAudio();});
$('family-view').onclick=()=>setView('read');
window.addEventListener('pagehide',()=>call?.dispose());
document.addEventListener('visibilitychange',()=>{if(document.hidden&&call){call.stop();note('页面切到后台，聊天已结束；回来后可继续。');}});
async function openEditor(id,seekSeconds=null){
  const profileId=profile.id;const next=await api('/life/sessions/'+id);if(profile.id!==profileId)return;
  if(next.mode==='listening'){await listening.openSession(id,seekSeconds||0);return;}
  editing=next;$('edit-title').value=editing.title;
  const raw=editing.turns.map(t=>`${t.role==='user'?profile.name:'AI'}：${t.text}`).join('\n\n');$('raw').textContent=raw||'尚无转写记录。';$('edit-text').value=editing.edited_text||raw;
  $('editor-meta').textContent=`${editing.created_at} UTC · 校对版本 ${editing.version}。AI 音轨是生成记录，可能包含被打断后未播放的部分。`;
  $('audio').pause();$('audio').hidden=true;$('audio').removeAttribute('src');
  for(const id of ['edit-title','edit-text','save-edit'])$(id).disabled=profile.role==='reader';
  $('ask-from-session').hidden=profile.role==='reader';$('process-session').hidden=profile.role==='reader';$('raw-turns').replaceChildren();
  for(const t of editing.turns.filter(t=>t.role==='user')){const b=button(`约 ${Math.floor(t.seconds)} 秒：${t.text.slice(0,18)}`,async()=>{await listen('user');$('audio').currentTime=t.seconds;});b.type='button';$('raw-turns').append(b);}
  $('editor').showModal();
  if(seekSeconds!==null){try{await listen('user',seekSeconds);}catch(e){note(e.message);}}
}
$('close-editor').onclick=()=>$('editor').close();
$('editor').addEventListener('close',()=>{$('audio').pause();if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=null;});
$('edit-form').onsubmit=action(async()=>{editing=await api('/life/sessions/'+editing.id,'PUT',{title:$('edit-title').value,edited_text:$('edit-text').value,version:editing.version});$('editor-meta').textContent=`校对版本 ${editing.version} 已保存；原始转写不变。`;await history();});
async function listen(track,seek=0){
  const response=await fetch(`${BASE}/api/life/sessions/${editing.id}/audio?track=${track}`,{credentials:'same-origin',headers:token?{Authorization:'Bearer '+token}:{}});
  if(!response.ok)throw Error('这次聊天没有可回听的音频。');if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(await response.blob());const ready=new Promise((resolve,reject)=>{$('audio').onloadedmetadata=resolve;$('audio').onerror=()=>reject(Error('音频加载失败'));});$('audio').src=audioUrl;$('audio').hidden=false;await ready;$('audio').currentTime=seek;await $('audio').play();
}
$('listen-user').onclick=action(()=>listen('user'));$('listen-ai').onclick=action(()=>listen('assistant'));
$('export').onclick=()=>{const text=`${$('edit-title').value}\n\n${$('edit-text').value}\n\n原始转写\n${$('raw').textContent}`;const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='人生之书-'+editing.id+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const book=createBook({api,profile:()=>profile,openSession:openEditor,note});
const chapters=createChapters({api,profile:()=>profile,openSession:openEditor,note,canConfigure:()=>!!provider?.can_configure});
async function authorizedAudio(path){
  if(call||listening.active)throw Error('结束当前录音后，再回听原声。现在可以继续看文字。');
  const response=await fetch(BASE+'/api'+path,{credentials:'same-origin',headers:{...(guestMode?{'X-Life-Link':guestToken}:{}),...(token?{Authorization:'Bearer '+token}:{})}});
  if(!response.ok)throw Error('这段录音暂时无法播放。');return response.blob();
}
const listening=createListening({api,profile:()=>profile,isGuest:()=>guestMode,note,fetchAudio:authorizedAudio,canConfigure:()=>!!provider?.can_configure,
  canStart:()=>!call,
  onBusy:value=>{
    if(value)document.querySelectorAll('audio').forEach(audio=>audio.pause());
    for(const id of ['profiles','new-profile','topic','save-profile','talk-view','read-view','family-view'])$(id).disabled=value||!!call;
    $('start').disabled=value||!provider?.configured||profile?.role==='reader';
    $('narrator-actions').hidden=value||view!=='talk';
    document.body.classList.toggle('listening-active',value);
  },
  onSaved:async()=>{await narrator.history();if(!guestMode){await history();await book.load();await chapters.load();}}
});
const narrator=createNarrator({api,profile:()=>profile,isGuest:()=>guestMode,note,fetchAudio:authorizedAudio,openListening:(id,seconds)=>listening.openSession(id,seconds)});
async function refreshNames(){
  if(!profile)return;const id=profile.id;const updated=await api(guestMode?'/life/guest':`/life/profiles/${id}`);if(profile.id!==id)return;
  profile.interviewer_name=updated.interviewer_name;profile.narrator_name=updated.narrator_name;
  $('interviewer-label').textContent=(profile.interviewer_name||'拾光')+' · AI';
}
$('toggle-conversation').onclick=()=>{
  const collapsed=document.body.classList.toggle('conversation-collapsed');$('live-caption').hidden=!collapsed;
  $('toggle-conversation').setAttribute('aria-expanded',String(!collapsed));$('toggle-conversation').textContent=collapsed?'展开对话':'收起对话';
};
$('chat-tab').onclick=()=>setView('talk');$('my-book-tab').onclick=()=>{setView('book');narrator.book().catch(e=>note(e.message));};
function setView(next){
  $('listening-workspace').hidden=next!=='talk';
  view=next;$('book-panel').hidden=view!=='read';$('interview-panel').hidden=view!=='talk';$('my-book-panel').hidden=view!=='book';
  $('narrator-nav').hidden=view==='read';$('narrator-actions').hidden=view==='read';
  document.body.classList.toggle('simple',guestMode||view!=='read');$('family-view').hidden=guestMode||view==='read';
  document.querySelector('h1').textContent=profile?profile.name+'的人生之书':'一起聊聊从前';
  document.querySelector('.intro').textContent=view==='read'?'原声、原文、事实与故事，分别保存。':'您慢慢讲，我认真听。';
  $('interviewer-label').textContent=(profile?.interviewer_name||'拾光')+' · AI';
  $('talk-view').className=view==='talk'?'':'secondary';$('read-view').className=view==='read'?'':'secondary';
  $('chat-tab').setAttribute('aria-pressed',String(view==='talk'));$('my-book-tab').setAttribute('aria-pressed',String(view==='book'));
  $('chat-tab').className=view==='talk'?'':'secondary';$('my-book-tab').className=view==='book'?'':'secondary';
  if(view==='book'&&profile)narrator.book().catch(e=>note(e.message));
  if(!guestMode){const url=new URL(location.href);url.searchParams.set('view',view);window.history.replaceState(null,'',url);}
}
$('talk-view').onclick=()=>setView('talk');$('read-view').onclick=()=>setView('read');
async function refreshGuest(){
  profile=await api('/life/guest');provider={configured:profile.configured};
  document.body.classList.add('guest','signed-in');$('workspace').hidden=false;$('profile-work').hidden=false;$('login-panel').hidden=true;
  $('identity').textContent='专属聊天入口';document.querySelector('h1').textContent=profile.name+'的人生之书';
  document.querySelector('.intro').textContent='您慢慢讲，我认真听。';$('topic').value='';$('topic').readOnly=true;document.querySelector('.brand').removeAttribute('href');
  $('start').disabled=!profile.configured;$('book-panel').hidden=true;$('provider-panel').hidden=true;
  setView('talk');await loadPreferences();await listening.load();
  if(!profile.configured)note('语音服务暂未准备好，请联系家人。');
}
const preferenceLabels={interviewer_name:'AI的称呼',narrator_name:'我的称呼',pace:'说话节奏',question_style:'提问方式',topic:'优先聊的话题'};
const preferenceValues={slow:'慢一点',normal:'平常速度',one_at_a_time:'一次只问一个问题',home:'住处与家乡',hobbies:'平时的爱好',concrete:'具体的小问题',open:'留出自由讲述的空间',childhood:'小时候',family:'家里的人',parents:'父母',school:'读书',work:'工作与劳动',daily_life:'日常生活',everyday:'日常生活',marriage:'婚恋',free:'随意聊'};
async function loadPreferences(){
  if(!profile)return;const id=profile.id;
  const base=guestMode?'/life/guest/preferences':`/life/profiles/${id}/preferences`;
  const list=await api(base);if(profile.id!==id)return;
  const target=$('preference-list');target.replaceChildren();
  if(!list.length){target.textContent='还没有需要特别记住的聊天习惯。';return;}
  for(const p of list){
    const row=document.createElement('div');row.className='preference-row';
    const label=document.createElement('strong');
    label.textContent=p.key.startsWith('avoid_topic:')?'暂不主动问：'+(preferenceValues[p.key.split(':')[1]]||p.key.split(':')[1]):(preferenceLabels[p.key]||p.key)+'：'+(preferenceValues[p.value]||p.value);
    const source=document.createElement('p');source.textContent='您说：“'+p.source_text+'”';row.append(label,source);
    if(!guestMode&&p.session)row.append(button('看这段原文',()=>openEditor(p.session),'secondary'));
    if(guestMode||profile.role!=='reader')row.append(button('撤销这条',async()=>{await api(base+'/'+encodeURIComponent(p.id),'DELETE');await loadPreferences();await refreshNames();note('已撤销，下次聊天不再采用这条习惯。');}));
    target.append(row);
  }
}
async function narratorLinks(){
  const id=profile.id;const links=await api(`/life/profiles/${id}/links`);if(profile.id!==id)return;$('narrator-links').replaceChildren();
  for(const l of links){const row=document.createElement('div');row.textContent=`入口 ${l.id} · ${l.revoked?'已撤销':'有效至 '+new Date(l.expires*1000).toLocaleDateString()}`;if(!l.revoked)row.append(button('撤销',async()=>{await api(`/life/profiles/${profile.id}/links/${l.id}`,'DELETE');await narratorLinks();}));$('narrator-links').append(row);}
}
$('create-link').onclick=action(async()=>{
  const link=await api(`/life/profiles/${profile.id}/links`,'POST',{});
  $('generated-link').value=`${location.origin}${BASE}/life/?view=guest#invite=${link.token}`;$('generated-link-label').hidden=false;await narratorLinks();
});
$('generated-link').onclick=()=>$('generated-link').select();
$('process-session').onclick=action(async()=>{const job=await api(`/life/sessions/${editing.id}/process`,'POST',{});$('editor-meta').textContent=job.status==='completed'?'本次已生成草稿，请在待审核区查看。':'已加入后台整理队列，原文和录音不会被修改。';await book.load();});
$('ask-from-session').onclick=()=>{const id=editing.id;$('editor').close();book.newItem('question',id);};
$('revisions').onclick=action(async()=>{const list=await api(`/life/sessions/${editing.id}/revisions`);book.showSource('校对历史',list.map(r=>`版本 ${r.version} · ${r.created_at} UTC\n${r.text}`).join('\n\n')||'还没有校对历史。');});
$('download-audio').onclick=action(async()=>{const response=await fetch(`${BASE}/api/life/sessions/${editing.id}/audio`,{credentials:'same-origin',headers:token?{Authorization:'Bearer '+token}:{}});if(!response.ok)throw Error('暂无录音');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=editing.id+'-原声.wav';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
refresh().catch(e=>{if(guestMode){note(e.message);$('login-panel').hidden=true;}else{$('login-panel').hidden=true;$('workspace').hidden=true;if(user)note(e.message);else $('login-panel').hidden=false;}});

setInterval(()=>{if(!guestMode&&profile&&!call&&!listening.active&&!document.hidden&&!$('item-editor').open&&!$('editor').open)book.load().catch(()=>{});},15000);

if(!guestMode){
  api('/auth/providers').then(p=>{$('sms-send').disabled=!p.sms;$('sms-note').textContent=p.sms?'验证码5分钟有效。':'手机号登录等待签名与模板开通，当前请使用账号或专属链接。';}).catch(()=>{});
}
$('sms-send').onclick=action(async()=>{await api('/auth/sms/send','POST',{phone:$('sms-phone').value});$('sms-note').textContent='验证码已发送，请在5分钟内填写。';});
$('sms-form').onsubmit=action(async()=>{const r=await api('/auth/sms/login','POST',{phone:$('sms-phone').value,code:$('sms-code').value});token=r.session_id;try{localStorage.setItem('paperlife_session_token',token);}catch{}$('sms-code').value='';await refresh();});
