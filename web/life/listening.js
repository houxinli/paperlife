import {ListeningCall} from './listening-audio.js';

// 续录：连续几段都没连上或撑不到一分钟（网络太差会不停切出很短的段），或断开超过时限仍接不上，就停止录音。
const MAX_FAILED_PARTS=3,HEALTHY_PART_MS=60000,RECONNECT_MS=180000;
// 这些错误重试也不会好（未登录、无权限、档案不存在、请求无效、当日次数用完）。
const FATAL_STATUS=new Set([401,403,404,422,429]);
// 两端：通话那头的声音经过通话编码和扬声器，手机旁边的人直接对着麦克风说。服务器按句判断，拿不准的带“?”。
const SIDE_LABELS={far:'通话那头',near:'手机旁边'};
const PROGRESS_POLL_MS=5000,PROGRESS_POLLS=120;

// 关键路径：首页一张检查卡和一个按钮 → 全屏录音页（计时、收音、两端字幕、AI 笔记）→ 小结页一键确认说话人 → AI 整理进度。
export function createListening({api,profile,isGuest=()=>false,note=()=>{},fetchAudio,playback,onSaved=()=>{},onOpenBook=()=>{},canConfigure=()=>false,canStart=()=>true,onBusy=()=>{}}){
  const root=document.getElementById('listening-workspace'),review=document.getElementById('listening-review'),settings=document.getElementById('listening-settings');
  let epoch=0,reviewEpoch=0,initialSeek=0,loadedProfile=null,call=null,controller=null,recording=false,config=null,audio=null,audioUrl=null,participantCounter=0,sampleAudio=null;
  const el=(tag,text='',id)=>{const n=document.createElement(tag);n.textContent=text;if(id)n.id=id;return n;};
  const context=()=>({id:profile()?.id,epoch,guest:isGuest()});
  const valid=c=>c.epoch===epoch&&c.id===profile()?.id&&c.guest===isGuest();
  const editable=()=>!isGuest()&&['owner','editor'].includes(profile()?.role);
  const subjectName=()=>(profile()?.name||'档案主体').slice(0,80);
  const enc=encodeURIComponent;
  const detailPath=(id,c)=>c.guest?`/life/guest/listening/${enc(id)}`:`/life/listening/sessions/${enc(id)}`;
  const audioPath=(id,c)=>c.guest?`/life/guest/history/${enc(id)}/audio?track=user`:`/life/sessions/${enc(id)}/audio?track=user`;
  function button(label,fn,id){const b=el('button',label,id);b.type='button';b.className='secondary';b.onclick=fn;return b;}
  function field(parent,label,tag,id,value='',type){const l=el('label',label),n=el(tag,'',id);n.value=value;if(type)n.type=type;l.append(n);parent.append(l);return n;}
  function status(n,text){n.textContent=text;}
  function releaseAudio(){audio?.pause();if(audio)audio.src='';if(audioUrl)URL.revokeObjectURL(audioUrl);audio=null;audioUrl=null;sampleAudio=null;}
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
    try{const result=await api(c.guest?'/life/guest/listening/provider':'/life/listening/provider');if(!valid(c))return;config=result;renderHome(c);renderSettings(c);}
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
  // 名单：档案本人是通话那头的默认人选，“我”是旁边的默认人选；开始前在“更多设置”里写的人也在里面。
  function crowd(roster){
    const people=roster.map(p=>({...p}));
    if(!people.some(p=>p.is_subject))people.unshift({id:'subject',name:subjectName(),relationship:'',is_subject:true});
    if(!people.some(p=>p.id==='me'||p.name==='我'))people.push({id:'me',name:'我',relationship:'',is_subject:false});
    return people.slice(0,30);
  }
  function renderHome(c,message=''){
    const name=subjectName(),wrap=el('section');wrap.className='listen-home';
    wrap.append(el('h2',c.guest?'记录一次通话':`记录和${name}的通话`));
    const steps=el('ol');steps.className='listen-steps';
    for(const text of [`用手机或电脑和${c.guest?'家人':name}视频，打开外放`,'把这台设备放在扬声器旁边','插上电源；录音时屏幕会保持常亮'])steps.append(el('li',text));
    const state=el('p',message,'listening-state');state.setAttribute('role','status');
    const hint=el('p','','listening-hint');hint.className='muted';
    const enabled=!!config.enabled&&!!config.configured;
    if(!enabled)state.textContent='旁听识别尚未开通。请管理员启用服务并检查现有语音凭据。';
    const form=el('form','','listening-record-form');
    const consent=field(form,c.guest?'通话双方都知道这次会被录音和识别':`${name}和我都知道这次通话会被录音和识别`,'input','listening-consent','','checkbox');
    const start=el('button','开始记录','listening-start');start.type='submit';start.className='primary';start.disabled=!enabled||(!c.guest&&!editable());form.append(start);
    const more=el('details');more.className='listen-more';more.append(el('summary','更多设置'));
    const title=field(more,'记录标题（可选）','input','listening-title');title.maxLength=180;
    // 专属链接是长辈自己录，笔记是给提问的家人看的，不提供。
    const notesToggle=c.guest?null:field(more,'AI 实时笔记：边听边整理要点和可以追问的问题（调用文本模型，只给你看，不进档案）','input','listening-notes-enabled','','checkbox');if(notesToggle)notesToggle.checked=true;
    const roster=el('details');roster.append(el('summary','其他参与者（可选）'),el('p',`通话那头默认是${name}，旁边默认是“我”。还有别人的话可以先写上，确认说话人时能直接选。`));more.append(roster);
    const participants=participantEditor(roster,[],true,'listening-roster');
    form.append(more);
    const recent=el('section','','listening-recent');recent.className='listen-recent';
    wrap.append(steps,form,state,hint,recent);root.replaceChildren(wrap);
    form.onsubmit=async e=>{
      e.preventDefault();if(!valid(c)||recording||start.disabled)return;
      if(!consent.checked){status(state,'请先确认通话双方都知道在录音。');return;}
      if(!canStart()){status(state,'请先结束正在进行的语音聊天或回听，再开始记录。');return;}
      const people=participants.values();if(people.some(p=>!p.name)){status(state,'请填写参与者称呼，或删掉空白的一行。');return;}
      releaseAudio();setBusy(true);
      await record(c,{baseTitle:title.value.trim()||(c.guest?'旁听通话':`和${name}的通话`),roster:people,notes:!!notesToggle?.checked});
    };
    if(!c.guest)void loadRecent(c,recent);
  }
  async function loadRecent(c,box){
    try{
      const [sessions,jobs]=await Promise.all([api(`/life/profiles/${enc(c.id)}/sessions`),editable()?api(`/life/profiles/${enc(c.id)}/processing`).catch(()=>[]):Promise.resolve([])]);
      if(!valid(c)||recording)return;
      const calls=sessions.filter(s=>s.mode==='listening'&&['ended','interrupted'].includes(s.status)).slice(0,8);
      box.replaceChildren();if(!calls.length)return;
      box.append(el('h3','最近的通话'));
      for(const s of calls){
        const job=jobs.find(j=>j.session===s.id);
        const badge=!s.version?'待确认说话人':job?.status==='completed'?'已整理':job?.status==='failed'?'整理失败':job?'整理中':'已确认';
        const row=button('',()=>showSummary(c,[s.id]));row.className=s.version?'secondary listen-call':'listen-call';
        const title=el('span',s.title),meta=el('small',`${s.created_at} UTC · ${badge}`);row.append(title,meta);box.append(row);
      }
    }catch{}
  }
  // 一次记录由一段或多段录音组成：某段到时长上限、断网、服务重启或识别中断时，麦克风不停，
  // 换新票据接着录下一段（各段是独立的录音档案）。用户点结束、页面切后台、无权限或长时间接不上时才真正结束。
  async function record(c,{baseTitle,roster,notes:notesOn}){
    const people=crowd(roster),names={far:people.find(p=>p.is_subject)?.name||subjectName(),near:people.find(p=>p.id==='me'||p.name==='我')?.name||'我'};
    const stage=el('section');stage.className='listen-stage';
    const bar=el('div');bar.className='listen-bar';const dot=el('span','●');dot.className='listen-dot';
    const timer=el('strong','0:00','listening-timer');const state=el('p','正在申请麦克风并连接旁听服务…','listening-state');state.setAttribute('role','status');
    const end=button('结束并保存',stop,'listening-stop');end.className='danger';bar.append(dot,timer,state,end);
    const meter=el('div','','listening-level');meter.className='listen-level';const fill=el('span');meter.append(fill);
    const hint=el('p','','listening-hint');hint.className='muted';
    const live=el('div','','listening-live');live.className='listening-live';live.setAttribute('role','log');live.setAttribute('aria-live','polite');
    const notesBox=el('section','','listening-notes');notesBox.className='listening-notes';notesBox.hidden=!notesOn;
    const body=el('div');body.className='listen-body';body.append(live,notesBox);stage.append(bar,meter,hint,body);root.replaceChildren(stage);
    const tip=el('p',`实时文字是边听边识别的暂定结果；“${SIDE_LABELS.far}”默认是${names.far}，“${SIDE_LABELS.near}”默认是${names.near}，结束后再确认。`);tip.className='muted';live.append(tip);
    if(notesOn){const waiting=el('p','AI 笔记：录到一些内容后，大约每分钟更新一次。');waiting.className='muted';notesBox.append(waiting);}
    const current=new ListeningCall(onEvent);call=current;
    const startedAt=Date.now(),sids=[];
    let part=0,sid=null,saved=false,ready=false,readyAt=0,limited=false,everReady=false,failedParts=0,lostAt=0,retry=null,finished=false,seconds=0,partSeconds=0,livePart=null,sides={};
    const owned=()=>!finished&&valid(c)&&call===current;
    const awake=keepScreenOn(()=>{if(owned())hint.textContent='这台设备不能由网页保持亮屏。请在系统设置里暂时关闭自动锁屏，录完再改回来；锁屏会结束录音。';});
    const clock=setInterval(()=>{if(owned())timer.textContent=duration((Date.now()-startedAt)/1000);},1000);
    // 收音条：直接显示麦克风电平，一眼看出那头的声音有没有进来。
    const levels=setInterval(()=>{const db=current.level?.();if(owned()&&fill.style&&Number.isFinite(db))fill.style.width=`${Math.max(0,Math.min(100,(db+60)/50*100))}%`;},200);
    controller={stop:userStop,abandon};
    function release(){finished=true;clearTimeout(retry);clearInterval(clock);clearInterval(levels);awake.release();if(controller?.stop===userStop)controller=null;}
    function finish(message){
      if(finished)return;const mine=valid(c)&&call===current;release();
      if(!current.closed)current.dispose();
      if(!mine)return;
      call=null;setBusy(false);
      if(sids.length&&!c.guest)void showSummary(c,sids,{message,seconds:seconds+partSeconds});else renderHome(c,message);
    }
    // 切换人物时：仍让服务器收尾保存这一段，但不再更新界面或续录。
    function abandon(){if(finished)return;release();if(!current.closed)current.stop();}
    function userStop(){
      if(!owned()||current.stopping)return;
      clearTimeout(retry);current.stop();end.disabled=true;
      if(current.closed)finish(everReady?'记录已结束，已收到的录音都已保存。':'记录已结束。');
      else status(state,'正在保存最后一点识别…');
    }
    // 实时记录：整场字幕从头保留到尾，跨段不清空。服务器只发从第一处变化开始的分句（from/total）和有变化的两端归边。
    function beginLivePart(){
      const box=el('section'),header=part>1?el('p',`第${part}段`):null;box.className='listening-live-part';
      if(header){header.className='muted';box.append(header);}
      livePart={box,header,bubbles:[]};sides={};live.append(box);
    }
    function who(speaker,id){
      const side=sides[id];if(!side)return {text:speaker==='unknown'?'未知声音':`声音组 ${speaker}`,side:''};
      const base=side.replace('?','');return {text:names[base]+(side.endsWith('?')?'？':''),side:base};
    }
    function paint(bubble){const label=who(bubble.speaker,bubble.id);bubble.who.textContent=label.text;bubble.node.className=label.side?`listening-bubble side-${label.side}`:'listening-bubble';}
    function showTranscript(event){
      if(!livePart)beginLivePart();
      Object.assign(sides,event.sides||{});
      const bubbles=livePart.bubbles,segments=Array.isArray(event.segments)?event.segments:[];
      const from=Math.min(Number.isInteger(event.from)?event.from:0,bubbles.length),total=Number.isInteger(event.total)?event.total:from+segments.length;
      const stick=!(live.scrollHeight-live.scrollTop-live.clientHeight>48);
      segments.forEach((segment,offset)=>{
        let bubble=bubbles[from+offset];
        if(!bubble){const node=el('p'),label=el('strong'),text=el('span');node.append(label,text);bubble={node,who:label,text};bubbles.push(bubble);livePart.box.append(node);}
        bubble.id=segment.id;bubble.speaker=segment.speaker_id;bubble.text.textContent=segment.text;
      });
      if(bubbles.length>total){bubbles.length=total;livePart.box.replaceChildren(...(livePart.header?[livePart.header]:[]),...bubbles.map(b=>b.node));}
      for(const bubble of bubbles)paint(bubble);
      if(stick)live.scrollTop=live.scrollHeight;
    }
    function showNotes(event){
      if(!notesOn)return;
      notesBox.hidden=false;
      if(!notesBox.points){
        const title=el('p','AI 笔记 · 仅供参考，不进档案');title.className='muted';
        notesBox.points=el('ul');notesBox.questions=el('ul');notesBox.error=el('p');notesBox.error.className='muted';
        notesBox.replaceChildren(title,el('h4','可以追问'),notesBox.questions,el('h4','要点'),notesBox.points,notesBox.error);
      }
      notesBox.error.textContent=event.error||'';
      for(const point of event.points||[])notesBox.points.append(el('li',point.speaker?`${point.speaker}：${point.text}`:point.text));
      if(Array.isArray(event.questions)&&event.questions.length)notesBox.questions.replaceChildren(...event.questions.map(q=>{const item=el('li',q.text);if(q.why){const why=el('small',`（${q.why}）`);item.append(why);}return item;}));
    }
    function total(){return duration(seconds+partSeconds);}
    function onEvent(event){
      if(!owned())return;
      if(event.type==='ready'){ready=everReady=true;readyAt=Date.now();lostAt=0;if(sid&&!sids.includes(sid))sids.push(sid);beginLivePart();if(notesOn)current.sendControl({type:'notes',enabled:true});status(state,`正在记录${part>1?` · 第${part}段`:''}`);}
      if(event.type==='listening_transcript')showTranscript(event);
      if(event.type==='listening_notes')showNotes(event);
      if(event.type==='ending'){if(current.stopping){end.disabled=true;status(state,'正在保存最后一点识别…');}else{limited=true;status(state,`第${part}段已满两小时，正在接着录下一段…`);}}
      if(event.type==='saved'){
        if(event.recognition_status){saved=true;Promise.resolve(onSaved(sid)).catch(()=>{});}
        else{partSeconds=event.seconds||0;if(!current.stopping)status(state,`正在记录${part>1?` · 第${part}段`:''}`);}
      }
      if(event.type==='notice'||event.type==='error')status(state,event.message);
      if(event.type==='ended'||event.type==='disconnected'){
        seconds+=partSeconds;partSeconds=0;
        if(current.stopping||current.closed){finish(event.type==='ended'&&saved&&event.recognition_status==='complete'?'录音和识别都已保存。':'连接已结束，最后一点识别可能不完整，已收到的录音都已保存。');return;}
        partLost(event.type==='ended');
      }
    }
    function partLost(closedByServer){
      const wasReady=ready;ready=false;
      if(!everReady){finish('没能开始记录：连接没有建立。请检查服务配置后重试。');return;}
      if(wasReady&&(limited||Date.now()-readyAt>=HEALTHY_PART_MS))failedParts=0;else failedParts++;
      if(!lostAt)lostAt=Date.now();
      if(failedParts>=MAX_FAILED_PARTS||Date.now()-lostAt>RECONNECT_MS){finish(`多次尝试都没能接上，已停止录音。已收到的 ${total()} 录音都已保存，请稍后再开始。`);return;}
      status(state,closedByServer&&wasReady?`第${part}段已保存，正在接着录下一段…（已录 ${total()}）`:`连接中断，正在重新连接…录音不会停（已录 ${total()}）`);
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
        // 专属链接的长辈不能修改身份，只带开始时填的名单；家人带上补全后的名单，确认说话人时可直接选。
        const result=await api(c.guest?'/life/guest/listening':`/life/profiles/${enc(c.id)}/listening`,'POST',{title:number>1?`${baseTitle}（第${number}段）`:baseTitle,participants:c.guest?roster:people,consent:true});
        if(!owned()||stopped())return;
        part=number;sid=result.id||result.session?.id;saved=false;ready=false;limited=false;
        current.connect(result.ticket||result.session?.ticket);
      }catch(error){
        if(!owned()||stopped())return;
        if(!everReady||FATAL_STATUS.has(error.status)){finish('没能开始记录：'+error.message);return;}
        // 上一段还在收尾（409）、网络或服务暂时不可用时，在时限内继续重试。
        if(Date.now()-lostAt>RECONNECT_MS){finish(`多次尝试都没能接上，已停止录音。已收到的 ${total()} 录音都已保存，请稍后再开始。`);return;}
        status(state,`正在重新连接…（已录 ${total()}）`);schedule();
      }
    }
    try{
      await current.prepare();
      if(!owned()||current.stopping){finish('记录已取消。');return;}
    }catch(error){finish('没能开始记录：'+(error?.message||'无法使用麦克风'));return;}
    await startPart();
  }
  // 小结页：时长与句数 → 一键确认两端是谁（附原话可回听）→ AI 整理进度 → 去看整理结果。
  async function showSummary(c,sids,{message='',seconds=null}={}){
    const wrap=el('section');wrap.className='listen-summary';
    const state=el('p',message,'listening-state');state.setAttribute('role','status');
    const stats=el('p','正在载入这次通话…','listening-summary-stats');stats.className='muted';
    const back=button('再记录一次',()=>{if(valid(c))renderHome(c);},'listening-again');
    // 确认卡和整理进度都放进 body，“再记录一次”始终在最后。
    const body=el('div');body.className='listen-summary-body';
    wrap.append(el('h2','通话已保存'),stats,state,body);root.replaceChildren(wrap);
    let details;
    try{details=await Promise.all(sids.map(id=>api(detailPath(id,c))));}
    catch(error){if(valid(c)){stats.textContent='';status(state,'载入失败：'+error.message);wrap.append(back);}return;}
    if(!valid(c)||recording)return;
    const segments=details.flatMap(d=>d.segments||[]);
    const length=seconds??details.reduce((sum,d)=>sum+Math.max(0,...(d.segments||[]).map(s=>s.end_ms||0))/1000,0);
    stats.textContent=`${duration(length)} · ${segments.length} 句${details.length>1?` · 共 ${details.length} 段`:''}`;
    if(c.guest||!editable()){wrap.append(back);return;}
    if(details.some(d=>['pending','partial'].includes(d.recognition_status))){status(state,'识别还在收尾，稍后点“最近的通话”再确认。');wrap.append(back);return;}
    const pending=details.filter(d=>!d.session.version);
    if(pending.length)renderConfirm(c,body,pending,sids);else renderProgress(c,body,sids);
    wrap.append(back);
  }
  function renderConfirm(c,wrap,details,sids){
    const box=el('section','','listening-confirm');box.className='listen-confirm';box.append(el('h3','确认说话人'));
    const people=crowd(details[0].participants||[]);
    const separated=details.some(d=>d.suggestion?.separated);
    const message=el('p','','listening-confirm-state');message.setAttribute('role','status');
    if(!separated){
      box.append(el('p','这次没能自动分出通话两端（可能两边声音太像，或者只有一个人在说话）。请逐句检查，指定每句是谁说的。'));
      for(const d of details)box.append(button(details.length>1?`逐句检查：${d.session.title}`:'逐句检查',()=>openSession(d.session.id)));
      wrap.append(box);return;
    }
    const pickers={};
    for(const side of ['far','near']){
      const row=el('div');row.className='listen-side';
      const label=el('label',`${SIDE_LABELS[side]}是`),select=el('select','',`listening-side-${side}`);label.append(select);row.append(label);
      const name=el('input','',`listening-side-name-${side}`);name.maxLength=80;name.placeholder='称呼';name.hidden=true;
      const add=button('添加',()=>{
        const value=name.value.trim();if(!value||people.length>=30)return;
        let id=`person-${++participantCounter}`;while(people.some(p=>p.id===id))id=`person-${++participantCounter}`;
        people.push({id,name:value,relationship:'',is_subject:false});for(const other of Object.values(pickers))fill(other);
        select.value=id;select.onchange();name.value='';name.hidden=add.hidden=true;
      });add.hidden=true;row.append(name,add);
      pickers[side]=select;fill(select);select.value=side==='far'?people.find(p=>p.is_subject).id:people.find(p=>p.id==='me'||p.name==='我')?.id||'';
      let chosen=select.value;
      select.onchange=()=>{if(select.value==='__new'){name.hidden=add.hidden=false;select.value=chosen;}else chosen=select.value;};
      // 每边附几句最长的原话，点一下回听，确认没分错。
      const samples=details.flatMap(d=>(d.segments||[]).filter(s=>d.suggestion?.sides?.[s.id]===side).map(s=>({...s,sid:d.session.id})));
      samples.sort((a,b)=>(b.text||'').length-(a.text||'').length);
      const list=el('ul');list.className='listen-samples';
      for(const s of samples.slice(0,3)){const item=el('li');item.append(button('▶',()=>playSample(c,s,message)),el('span',s.text));list.append(item);}
      if(!samples.length)list.append(el('li','这一边没有听出说话的句子。'));
      row.append(list);box.append(row);
    }
    function fill(select){
      const value=select.value;select.replaceChildren();
      for(const [id,text] of [...people.map(p=>[p.id,p.is_subject?`${p.name}（档案主体）`:p.name]),['__new','其他人…']]){const option=el('option',text);option.value=id;select.append(option);}
      select.value=value;
    }
    const unsure=details.reduce((sum,d)=>sum+(d.segments||[]).filter(s=>!d.suggestion?.sides?.[s.id]).length,0);
    if(unsure)box.append(el('p',`另有 ${unsure} 句没听出是谁（多是一句里两个人都说了话），确认后可以在“逐句检查”里指定；不指定的不会算到任何人头上。`));
    const save=el('button','确认，开始整理','listening-confirm-save');save.type='button';save.className='primary';
    save.onclick=async()=>{
      if(!valid(c)||save.disabled)return;
      const far=pickers.far.value,near=pickers.near.value;
      if(!far||!near||far==='__new'||near==='__new'){status(message,'请先选好两边分别是谁。');return;}
      save.disabled=true;status(message,'正在保存…');
      try{
        for(const d of details){
          const overrides=Object.fromEntries(Object.entries(d.suggestion?.sides||{}).map(([id,side])=>[id,{speaker_id:side}]));
          await api(detailPath(d.session.id,c),'PUT',{version:d.session.version,participants:people,bindings:{far,near},overrides});
        }
        if(!valid(c))return;
        Promise.resolve(onSaved(details[0].session.id)).catch(()=>{});
        box.remove?.();box.hidden=true;renderProgress(c,wrap,sids);
      }catch(error){if(valid(c)){save.disabled=false;status(message,'没能保存：'+error.message+'。可以稍后在“最近的通话”里再确认。');}}
    };
    const actions=el('div');actions.className='listen-actions';actions.append(save);
    for(const d of details)actions.append(button(details.length>1?`逐句检查：${d.session.title}`:'逐句检查',()=>openSession(d.session.id)));
    box.append(actions,message);wrap.append(box);
  }
  function renderProgress(c,wrap,sids){
    const box=el('section','','listening-progress');box.className='listen-progress';
    const line=el('p','已确认说话人。AI 正在整理这次通话：提取人物、事件和时间…');line.setAttribute('role','status');
    const open=button('去看整理结果',()=>onOpenBook(),'listening-open-book');open.hidden=true;
    box.append(line,open);wrap.append(box);
    let polls=0;
    const tick=async()=>{
      // 用户已离开小结页（页面换成了别的内容）就不再轮询。
      if(!valid(c)||recording||box.isConnected===false)return;
      try{
        const jobs=(await api(`/life/profiles/${enc(c.id)}/processing`)).filter(j=>sids.includes(j.session));
        if(!valid(c))return;
        if(jobs.length&&jobs.every(j=>j.status==='completed')){line.textContent='整理完成。草稿在“读与整理 · 待审核”里，确认后才会写进书里。';open.hidden=false;open.className='primary';return;}
        if(jobs.some(j=>j.status==='failed')){line.textContent='有一段整理失败了，可以在“读与整理”里重试。';open.hidden=false;return;}
      }catch{}
      if(++polls<PROGRESS_POLLS)setTimeout(tick,PROGRESS_POLL_MS);else{line.textContent='整理还在进行，稍后到“读与整理”里查看。';open.hidden=false;}
    };
    void tick();
  }
  async function playSample(c,segment,message){
    if(!valid(c))return;if(recording||!canStart()){status(message,'请先结束正在进行的录音或聊天。');return;}
    try{
      const ticket=playback?.capture();
      if(!sampleAudio||sampleAudio.sid!==segment.sid){
        const blob=await fetchAudio(audioPath(segment.sid,c));if(!valid(c))return;playback?.assert(ticket);
        releaseAudio();audio=el('audio');audioUrl=typeof blob==='string'?blob:URL.createObjectURL(blob);audio.src=audioUrl;sampleAudio={sid:segment.sid};
      }
      const player=audio,end=(segment.end_ms||0)/1000;
      player.ontimeupdate=()=>{if(player.currentTime>=end)player.pause();};
      const go=async()=>{player.currentTime=Math.max(0,(segment.start_ms||0)/1000);if(playback)await playback.play(player,ticket);else await player.play();};
      if(player.readyState>=1)await go();else{player.onloadedmetadata=()=>{go().catch(error=>status(message,error.message));};player.load?.();}
    }catch(error){if(valid(c))status(message,error.message);}
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
    const labelOf=id=>(data.speakers||[]).find(s=>s.id===id)?.label||(id==='unknown'?'未知声音':`声音组 ${id}`);
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
    for(const id of groupIds){const s=(data.speakers||[]).find(s=>s.id===id);addGroup(id,s?.label||labelOf(id),s?.participant_id);}
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
          const blob=await fetchAudio(audioPath(session.id,c));if(!current()||recording||!canStart())return;playback?.assert(playbackTicket);
          releaseAudio();audio=player;audioUrl=typeof blob==='string'?blob:URL.createObjectURL(blob);player.src=audioUrl;player.hidden=false;
          const play=async()=>{if(!current()||recording||!canStart())return;player.currentTime=Math.max(0,(s.start_ms||0)/1000);try{if(playback)await playback.play(player,playbackTicket);else await player.play();}catch(error){if(current())status(message,error.message);}};
          player.onloadedmetadata=play;player.load?.();
        }catch(error){if(current())status(message,error.message);}
      }));
      const raw=el('p',`原始：${s.original_speaker_id||s.speaker_id} · ${s.original_text??s.text}`);raw.className='listening-original';row.append(raw);
      const speaker=field(row,'这句话属于哪个声音组','select',`listening-segment-speaker-${index}`);
      for(const id of groupIds){const option=el('option',labelOf(id));option.value=id;speaker.append(option);}speaker.value=s.speaker_id;speaker.disabled=!canEdit;
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
function duration(value){const s=Math.floor(Math.max(0,Number(value)||0));const h=Math.floor(s/3600),m=Math.floor(s/60)%60,r=String(s%60).padStart(2,'0');return h?`${h}:${String(m).padStart(2,'0')}:${r}`:`${m}:${r}`;}
