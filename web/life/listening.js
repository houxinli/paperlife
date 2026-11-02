import {ListeningCall} from './listening-audio.js';

// 续录：连续几段都没连上，或断开超过时限仍接不上，就停止录音，避免无限重试。
const MAX_FAILED_PARTS=3,RECONNECT_MS=180000;
// 这些错误重试也不会好（未登录、无权限、档案不存在、请求无效、当日次数用完）。
const FATAL_STATUS=new Set([401,403,404,422,429]);

export function createListening({api,profile,isGuest=()=>false,note=()=>{},fetchAudio,playback,onSaved=()=>{},canConfigure=()=>false,canStart=()=>true,onBusy=()=>{}}){
  const root=document.getElementById('listening-workspace'),review=document.getElementById('listening-review'),settings=document.getElementById('listening-settings');
  let epoch=0,reviewEpoch=0,initialSeek=0,loadedProfile=null,call=null,controller=null,recording=false,config=null,audio=null,audioUrl=null,participantCounter=0;
  const el=(tag,text='',id)=>{const n=document.createElement(tag);n.textContent=text;if(id)n.id=id;return n;};
  const context=()=>({id:profile()?.id,epoch,guest:isGuest()});
  const valid=c=>c.epoch===epoch&&c.id===profile()?.id&&c.guest===isGuest();
  const editable=()=>!isGuest()&&['owner','editor'].includes(profile()?.role);
  const enc=encodeURIComponent;
  const detailPath=(id,c)=>c.guest?`/life/guest/listening/${enc(id)}`:`/life/listening/sessions/${enc(id)}`;
  function button(label,fn,id){const b=el('button',label,id);b.type='button';b.className='secondary';b.onclick=fn;return b;}
  function field(parent,label,tag,id,value='',type){const l=el('label',label),n=el(tag,'',id);n.value=value;if(type)n.type=type;l.append(n);parent.append(l);return n;}
  function status(n,text){n.textContent=text;}
  function releaseAudio(){audio?.pause();if(audio)audio.src='';if(audioUrl)URL.revokeObjectURL(audioUrl);audio=null;audioUrl=null;}
  function setBusy(value){if(recording===value)return;recording=value;onBusy(value);}
  function stop(){if(recording)controller?.stop();}
  review.addEventListener?.('close',()=>{if(!review.open){++reviewEpoch;releaseAudio();}});
  window.addEventListener('pagehide',stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  function clear(){
    ++epoch;++reviewEpoch;review.close?.();loadedProfile=null;controller?.abandon();controller=null;call=null;setBusy(false);config=null;releaseAudio();
    root.replaceChildren();review.replaceChildren();review.hidden=true;settings.replaceChildren();settings.hidden=true;
  }
  async function load(){
    if(recording)return;
    if(!profile()?.id){clear();return;}
    if(loadedProfile!==profile().id){clear();loadedProfile=profile().id;}
    const c=context();root.replaceChildren(el('p','正在检查旁听服务…'));settings.replaceChildren();settings.hidden=true;
    try{const result=await api(c.guest?'/life/guest/listening/provider':'/life/listening/provider');if(!valid(c))return;config=result;renderRecording(c);renderSettings(c);}
    catch(e){if(valid(c))root.replaceChildren(el('p','旁听服务暂不可用：'+e.message));}
  }
  function renderSettings(c){
    settings.replaceChildren();settings.hidden=true;if(c.guest||!canConfigure()||!config?.can_configure)return;
    settings.hidden=false;const d=el('details');d.append(el('summary','管理员 · 旁听识别服务'));
    d.append(el('p','启用旁听识别，复用服务器已有语音凭据。只记录与识别，不调用语音播报。'));
    const form=el('form','','listening-config-form'),enabled=field(form,'启用旁听识别','input','listening-enabled','','checkbox');enabled.checked=!!config.enabled;
    const save=el('button','保存旁听设置');save.type='submit';form.append(save);
    form.onsubmit=async e=>{e.preventDefault();if(!valid(c)||save.disabled)return;save.disabled=true;try{await api('/life/listening/provider','PUT',{enabled:enabled.checked});if(valid(c))await load();}catch(error){if(valid(c))note(error.message);}finally{save.disabled=false;}};
    d.append(form);settings.append(d);
  }
  function participantEditor(parent,initial,editableRows,prefix,onChange=()=>{}){
    const rows=[],box=el('div');box.className='listening-participants';parent.append(box);
    const add=p=>{
      const index=rows.length,row=el('div');row.className='listening-participant';
      const name=field(row,'称呼','input',`${prefix}-name-${index}`,p.name||'');name.maxLength=80;
      const relationship=field(row,'与档案主体的关系','input',`${prefix}-relationship-${index}`,p.relationship||'');relationship.maxLength=100;
      const subject=field(row,'这是档案主体','input',`${prefix}-subject-${index}`,'','checkbox');subject.checked=!!p.is_subject;
      let id=p.id;while(!id||rows.some(r=>r.id===id))id=`person-${++participantCounter}`;
      const item={id,name,relationship,subject};rows.push(item);
      subject.onchange=()=>{if(subject.checked)for(const other of rows)if(other!==item)other.subject.checked=false;};
      name.oninput=onChange;for(const n of [name,relationship,subject])n.disabled=!editableRows;box.append(row);onChange();
    };
    for(const p of initial)add(p);
    if(editableRows)parent.append(button('添加参与者',()=>{if(rows.length>=30){note('参与者最多30位。');return;}add({});}));
    return {rows,values:()=>rows.map(r=>({id:r.id,name:r.name.value.trim(),relationship:r.relationship.value.trim(),is_subject:!!r.subject.checked}))};
  }
  function renderRecording(c){
    root.replaceChildren();const d=el('details');d.append(el('summary','旁听家人的通话 · AI 全程静默'));
    d.append(el('p','在另一台设备上视频或打电话并打开外放，把这台手机或 iPad 放在旁边，记录双方声音。本页不会拨打电话，也不会播报。'));
    d.append(el('p','录音时请让本页一直开着，屏幕会尽量保持常亮；每段最长2小时，到时或断网后会自动接着录下一段。长时间录音请接上电源。'));
    const state=el('p','','listening-state');state.setAttribute('role','status');d.append(state);
    const hint=el('p','','listening-hint');hint.className='muted';d.append(hint);
    const enabled=!!config.enabled&&!!config.configured;
    state.textContent=enabled?'已准备好。声音分组和实时文字只是暂定，结束后请核实身份。':'旁听识别尚未开通。请管理员启用服务并检查现有语音凭据。';
    const form=el('form','','listening-record-form'),title=field(form,'记录标题（可选）','input','listening-title');title.maxLength=180;
    const roster=el('details');roster.append(el('summary','参与者称呼（可选）'),el('p','名单只是整理线索，不会自动识别或绑定声音身份。'));form.append(roster);
    const participants=participantEditor(roster,[],true,'listening-roster');
    const consent=field(form,'在场及通话双方都已知情并同意录音和识别','input','listening-consent','','checkbox');
    const start=el('button','开始静默旁听','listening-start');start.type='submit';start.disabled=!enabled||(!c.guest&&!editable());
    const end=button('结束旁听并保存',stop,'listening-stop');end.hidden=true;const live=el('div','','listening-live');live.className='listening-live';live.setAttribute('role','log');live.setAttribute('aria-live','polite');
    form.append(start,end);d.append(form,live);root.append(d);
    form.onsubmit=async e=>{
      e.preventDefault();if(!valid(c)||recording||start.disabled)return;
      if(!consent.checked){status(state,'请先确认在场及通话双方已知情并同意。');return;}
      if(!canStart()){status(state,'请先结束正在进行的语音聊天或回听，再开始旁听。');return;}
      const people=participants.values();if(people.some(p=>!p.name)){status(state,'请填写参与者称呼，或刷新后不填名单直接开始。');return;}
      releaseAudio();setBusy(true);start.disabled=true;end.hidden=false;end.disabled=false;d.open=true;hint.textContent='';status(state,'正在申请麦克风并连接旁听服务…');
      await record(c,{state,hint,start,end,live,enabled},title.value.trim()||'旁听通话',people);
    };
  }
  // 一次旁听由一段或多段录音组成：某段到时长上限、断网、服务重启或识别中断时，麦克风不停，
  // 换新票据接着录下一段（各段是独立的录音档案）。用户点结束、页面切后台、无权限或长时间接不上时才真正结束。
  async function record(c,ui,baseTitle,people){
    const {state,hint,start,end,live}=ui;
    const current=new ListeningCall(onEvent);call=current;
    let part=0,sid=null,saved=false,ready=false,everReady=false,failedParts=0,lostAt=0,retry=null,finished=false,seconds=0,partSeconds=0;
    const owned=()=>!finished&&valid(c)&&call===current;
    const awake=keepScreenOn(()=>{if(owned())hint.textContent='这台设备不能由网页保持亮屏。请在系统设置里暂时关闭自动锁屏，录完再改回来；锁屏会结束录音。';});
    controller={stop:userStop,abandon};
    function release(){finished=true;clearTimeout(retry);awake.release();if(controller?.stop===userStop)controller=null;}
    function finish(message){
      if(finished)return;const mine=valid(c)&&call===current;release();
      if(!current.closed)current.dispose();
      if(!mine)return;
      call=null;setBusy(false);start.disabled=!ui.enabled||(!c.guest&&!editable());end.hidden=true;status(state,message);
    }
    // 切换人物时：仍让服务器收尾保存这一段，但不再更新界面或续录。
    function abandon(){if(finished)return;release();if(!current.closed)current.stop();}
    function userStop(){
      if(!owned()||current.stopping)return;
      clearTimeout(retry);current.stop();end.disabled=true;
      if(current.closed)finish(everReady?'旁听已结束，已收到的录音都已保存。可到录音档案核实声音分组和文字。':'旁听已结束。');
      else status(state,'录音已停止，正在等待最终识别并保存…');
    }
    function total(){const s=Math.floor(seconds+partSeconds);return s<60?`${s} 秒`:s<3600?`${Math.floor(s/60)} 分 ${s%60} 秒`:`${Math.floor(s/3600)} 小时 ${Math.floor(s/60)%60} 分`;}
    function onEvent(event){
      if(!owned())return;
      if(event.type==='ready'){ready=everReady=true;failedParts=0;lostAt=0;status(state,`正在静默旁听${part>1?` · 第${part}段`:''} · AI 不会出声。`);}
      if(event.type==='listening_transcript'){
        live.replaceChildren();const segments=event.segments||[];
        if(event.total>segments.length)live.append(el('p',`共 ${event.total} 句，这里只显示最近 ${segments.length} 句。`));
        for(const segment of segments){const bubble=el('p');bubble.className='listening-bubble';bubble.append(el('strong',`${segment.speaker_id==='unknown'?'未知声音':`声音组 ${segment.speaker_id}`} · 暂定`),el('span',segment.text));live.append(bubble);}
      }
      if(event.type==='ending'){if(current.stopping){end.disabled=true;status(state,'录音已停止，正在等待最终识别并保存…');}else status(state,`第${part}段已到时长上限，正在保存并接着录下一段…`);}
      if(event.type==='saved'){
        if(event.recognition_status){saved=true;if(current.stopping)status(state,'服务器已保留录音与识别结果。正在结束连接…');Promise.resolve(onSaved(sid)).catch(()=>{});}
        else{partSeconds=event.seconds||0;status(state,`已收到 ${total()} 录音${part>1?` · 第${part}段`:''} · ${current.stopping?'正在等待最终识别并保存':'静默旁听中'}`);}
      }
      if(event.type==='notice'||event.type==='error')status(state,event.message);
      if(event.type==='ended'||event.type==='disconnected'){
        seconds+=partSeconds;partSeconds=0;
        if(current.stopping||current.closed){finish(event.type==='ended'&&saved&&event.recognition_status==='complete'?'旁听已保存。可到录音档案核实声音分组和文字。':'旁听连接已结束，最终识别可能不完整。请刷新录音档案检查已保留内容。');return;}
        partLost(event.type==='ended');
      }
    }
    function partLost(closedByServer){
      const wasReady=ready;ready=false;
      if(!everReady){finish('旁听未能开始：连接没有建立。请检查服务配置后重试。');return;}
      if(!wasReady)failedParts++;
      if(!lostAt)lostAt=Date.now();
      if(failedParts>=MAX_FAILED_PARTS||Date.now()-lostAt>RECONNECT_MS){finish(`多次尝试都没能接上，已停止录音。已收到的 ${total()} 录音都已保存，请稍后再开始。`);return;}
      status(state,closedByServer&&wasReady?`第${part}段已保存，正在接着录下一段…（已录 ${total()}）`:`连接中断，正在重新连接…（已录 ${total()}）`);
      schedule();
    }
    function schedule(){
      clearTimeout(retry);const waited=Date.now()-(lostAt||Date.now());
      retry=setTimeout(()=>{retry=null;void startPart();},waited<10000?1500:5000);
    }
    // 两段之间麦克风被系统暂停（来电、音频中断）时，录音对象会自行停止，没有连接可以等，直接收尾。
    function stopped(){if(!current.stopping&&!current.closed)return false;finish(`录音已停止。已收到的 ${total()} 录音都已保存。`);return true;}
    async function startPart(){
      if(!owned()||stopped())return;
      const number=part+1;
      try{
        const result=await api(c.guest?'/life/guest/listening':`/life/profiles/${enc(c.id)}/listening`,'POST',{title:number>1?`${baseTitle}（第${number}段）`:baseTitle,participants:people,consent:true});
        if(!owned()||stopped())return;
        part=number;sid=result.id||result.session?.id;saved=false;ready=false;
        current.connect(result.ticket||result.session?.ticket);
      }catch(error){
        if(!owned()||stopped())return;
        if(!everReady||FATAL_STATUS.has(error.status)){finish('旁听未能开始：'+error.message);return;}
        // 上一段还在收尾（409）、网络或服务暂时不可用时，在时限内继续重试。
        if(Date.now()-lostAt>RECONNECT_MS){finish(`多次尝试都没能接上，已停止录音。已收到的 ${total()} 录音都已保存，请稍后再开始。`);return;}
        status(state,`正在重新连接…（已录 ${total()}）`);schedule();
      }
    }
    try{
      await current.prepare();
      if(!owned()||current.stopping){finish('旁听已取消。');return;}
    }catch(error){finish('旁听未能开始：'+(error?.message||'无法使用麦克风'));return;}
    await startPart();
  }
  async function openSession(id,seconds=0){
    if(!profile()?.id)return;initialSeek=Math.max(0,Number(seconds)||0);const c=context(),request=++reviewEpoch;releaseAudio();review.hidden=false;if(!review.open)review.showModal?.();review.replaceChildren(el('p','正在载入旁听原文…'));
    try{const data=await api(detailPath(id,c));if(!valid(c)||request!==reviewEpoch)return;if(data.session?.profile&&data.session.profile!==c.id)throw Error('档案归属已变化，请刷新。');renderReview(data,c,request);review.scrollIntoView?.({block:'start',behavior:'smooth'});}
    catch(e){if(valid(c)&&request===reviewEpoch)review.replaceChildren(el('p',e.message));}
  }
  function renderReview(data,c,request,historical=false){
    review.replaceChildren();const current=()=>valid(c)&&request===reviewEpoch;
    const session=data.session,canEdit=!historical&&editable()&&data.can_edit===true&&['ended','interrupted'].includes(session.status)&&!['pending','partial'].includes(data.recognition_status);
    review.append(button('关闭旁听整理',()=>{++reviewEpoch;releaseAudio();review.close?.();review.hidden=true;}),el('h2',session.title||'旁听记录'),el('p',`旁听 · 版本 ${session.version} · ${data.recognition_status==='complete'?'识别已完成':'识别未完整确认，请结合原声核实'}`));
    review.append(el('p','声音组只是这次录音中的分组，不代表实名。把两个组绑定到同一参与者即可合并身份；未知声音可以保留。只有明确确认档案主体的声音后，才能作为主体素材写章节。'));
    const message=el('p','','listening-review-state');message.setAttribute('role','status');review.append(message);
    const form=el('form','','listening-edit-form');review.append(form);
    const bindings=[],segments=[];
    const refreshOptions=()=>{
      if(!participants)return;
      for(const {input} of bindings){const selected=input.value;input.replaceChildren();const unknown=el('option','未知 / 暂不绑定');unknown.value='';input.append(unknown);for(const p of participants.values()){const option=el('option',p.name||'尚未填写称呼');option.value=p.id;input.append(option);}input.value=selected;}
    };
    let participants=null;
    form.append(el('h3','参与者与档案主体'));
    participants=participantEditor(form,data.participants||[],canEdit,'listening',refreshOptions);
    // Subject ids stay simple for keyboard and tests; participant ids never enter HTML.
    participants.rows.forEach((r,i)=>r.subject.id=`listening-subject-${i}`);
    form.append(el('h3','声音组归属'));
    const groupIds=(data.speakers||[]).map(s=>s.id);if(!groupIds.includes('unknown'))groupIds.push('unknown');
    for(const s of data.segments||[])if(!groupIds.includes(s.speaker_id))groupIds.push(s.speaker_id);
    const groups=el('div');form.append(groups);
    function addGroup(id,label,participantId){
      if(groupIds.indexOf(id)<0)groupIds.push(id);
      const input=field(groups,label||`声音组 ${id}`,'select',`listening-binding-${bindings.length}`);input.disabled=!canEdit;bindings.push({id,input});refreshOptions();input.value=participantId||'';
      for(const item of segments){const option=el('option',label||`声音组 ${id}`);option.value=id;item.speaker.append(option);}
    }
    for(const id of groupIds){const s=(data.speakers||[]).find(s=>s.id===id);addGroup(id,s?.label||(id==='unknown'?'未知声音':`声音组 ${id}`),s?.participant_id);}
    if(canEdit)form.append(button('添加声音组（拆分归属）',()=>{let id=`manual-${++participantCounter}`;while(groupIds.includes(id))id=`manual-${++participantCounter}`;addGroup(id,`手动声音组 ${participantCounter}`,null);}));
    form.append(el('h3','逐句核对与回听'));
    const original=el('details');original.append(el('summary','原始识别与分组（保留不改）'),el('pre',(data.original_segments||[]).map(s=>`${formatTime(s.start_ms)} · ${s.speaker_id}：${s.text}`).join('\n')));form.append(original);
    if(initialSeek)form.append(el('p',`已定位至 ${formatTime(initialSeek*1000)} 附近，请点击对应原声回听。`));
    const player=el('audio','','listening-audio');player.controls=true;player.hidden=true;review.append(player);audio=player;
    for(const [index,s] of (data.segments||[]).entries()){
      const row=el('section');row.className='listening-segment';
      row.append(button(`回听 ${formatTime(s.start_ms)} 原声`,async()=>{
        if(!current())return;if(recording||!canStart()){status(message,'请先结束正在进行的录音或聊天。');return;}
        try{
          const playbackTicket=playback?.capture();
          const path=c.guest?`/life/guest/history/${enc(session.id)}/audio?track=user`:`/life/sessions/${enc(session.id)}/audio?track=user`;
          const blob=await fetchAudio(path);if(!current()||recording||!canStart())return;playback?.assert(playbackTicket);
          releaseAudio();audio=player;audioUrl=typeof blob==='string'?blob:URL.createObjectURL(blob);player.src=audioUrl;player.hidden=false;
          const play=async()=>{if(!current()||recording||!canStart())return;player.currentTime=Math.max(0,(s.start_ms||0)/1000);try{if(playback)await playback.play(player,playbackTicket);else await player.play();}catch(error){if(current())status(message,error.message);}};
          player.onloadedmetadata=play;player.load?.();
        }catch(error){if(current())status(message,error.message);}
      }));
      const raw=el('p',`原始：${s.original_speaker_id||s.speaker_id} · ${s.original_text??s.text}`);raw.className='listening-original';row.append(raw);
      const speaker=field(row,'这句话属于哪个声音组','select',`listening-segment-speaker-${index}`);
      for(const id of groupIds){const option=el('option',id==='unknown'?'未知声音':`声音组 ${id}`);option.value=id;speaker.append(option);}speaker.value=s.speaker_id;speaker.disabled=!canEdit;
      const text=field(row,'校对文字','textarea',`listening-segment-text-${index}`,s.text);text.rows=2;text.maxLength=20000;text.disabled=!canEdit;
      segments.push({id:s.id,speaker,text});form.append(row);
    }
    if(canEdit){
      const save=el('button','保存身份与逐句校订','listening-save');save.type='submit';form.append(save);
      form.onsubmit=async e=>{e.preventDefault();if(!current()||save.disabled)return;const people=participants.values();if(people.some(p=>!p.name)){status(message,'请填写每位参与者的称呼。');return;}save.disabled=true;
        const body={version:session.version,participants:people,bindings:Object.fromEntries(bindings.map(b=>[b.id,b.input.value||null])),overrides:Object.fromEntries(segments.map(s=>[s.id,{speaker_id:s.speaker.value,text:s.text.value}]))};
        try{await api(detailPath(session.id,c),'PUT',body);if(current()){await openSession(session.id);if(valid(c))Promise.resolve(onSaved(session.id)).catch(()=>{});}}
        catch(error){if(current())status(message,error.message+'。当前更正仍保留；若版本冲突，请先复制更正，再重新载入最新版本。');}finally{if(current())save.disabled=false;}
      };
    }
    review.append(button('重新载入最新版本',()=>openSession(session.id)));
    if(!c.guest&&(data.versions||[]).length){const history=el('details');history.append(el('summary','归属校订历史（只读）'));for(const v of data.versions)history.append(button(`查看版本 ${v.version}`,async()=>{try{const result=await api(detailPath(session.id,c)+`/versions/${enc(v.version)}`);if(current()){releaseAudio();renderReview(result,c,request,true);}}catch(error){if(current())status(message,error.message);}}));review.append(history);}
  }
  return {load,clear,openSession,get active(){return recording;}};
}
// 系统自动锁屏会让页面转入后台并结束录音，所以录音期间申请屏幕常亮；系统中途收回（如省电）时在前台再申请几次。
function keepScreenOn(onUnavailable){
  const wakeLock=globalThis.navigator?.wakeLock;let lock=null,active=true,renewals=0;
  async function request(){
    if(!active||lock)return;
    try{
      const next=await wakeLock.request('screen');
      if(!active){next.release?.()?.catch?.(()=>{});return;}
      lock=next;lock.addEventListener?.('release',()=>{lock=null;if(active&&!globalThis.document?.hidden&&renewals++<5)void request();});
    }catch{if(active)onUnavailable();}
  }
  if(wakeLock?.request)void request();else onUnavailable();
  return {release(){active=false;const held=lock;lock=null;held?.release?.()?.catch?.(()=>{});}};
}
function formatTime(ms){const seconds=Math.floor(Math.max(0,Number(ms)||0)/1000);return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;}
