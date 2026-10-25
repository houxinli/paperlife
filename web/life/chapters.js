/* 跨访谈写作：素材校订另存，草稿经人工确认后才入书。 */
export function createChapters({api,profile,openSession,note,canConfigure=()=>false}){
  const root=document.getElementById('chapter-workspace');
  const settings=document.getElementById('editor-model-settings');
  let epoch=0,detailEpoch=0,materialEpoch=0,selectedId=null,loadedProfileId=null,materials=[],sourceInputs=[];
  const el=(tag,text='',className='',id)=>{
    const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;if(id)n.id=id;return n;
  };
  const capture=()=>({id:profile()?.id,epoch,editable:['owner','editor'].includes(profile()?.role)});
  const valid=c=>!!c.id&&c.epoch===epoch&&profile()?.id===c.id;
  const pathId=id=>encodeURIComponent(id);
  const profilePath=c=>`/life/profiles/${pathId(c.id)}`;
  const chapterPath=id=>`/life/chapters/${pathId(id)}`;
  const message=(text,c,target)=>{if(valid(c)){if(target)target.textContent=text;else note(text);}};
  function button(text,fn,c,{id,primary=false}={}){
    const b=el('button',text,primary?'':'secondary',id);b.type='button';
    b.onclick=async()=>{
      if(!valid(c)||b.disabled)return;
      b.disabled=true;
      try{await fn();}catch(error){message(error.message,c);}finally{if(valid(c))b.disabled=false;}
    };return b;
  }
  function field(parent,label,tag,id,value='',type){
    const l=el('label',label),input=el(tag,'','',id);input.value=value;
    if(type)input.type=type;l.append(input);parent.append(l);return input;
  }
  function section(title,cls=''){
    const n=el('section','',cls);n.append(el('h3',title));return n;
  }
  const notice=el('p','','chapter-message','chapter-message');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
  const toolbar=el('div','','chapter-toolbar');
  const creation=el('div','','','chapter-creation');
  const materialEditor=el('div','','','chapter-material-editor');
  const list=el('div','','chapter-list','chapter-list');
  const jobs=el('div','','chapter-jobs','chapter-jobs');jobs.setAttribute('aria-live','polite');
  const detail=el('div','','','chapter-detail');
  root.replaceChildren(el('h3','跨访谈写章节'),el('p','聊天结束和素材校订后会自动提取事实；写成章节需要在这里选择素材并手动发起。AI 草稿需核实，明确确认后才收入人生之书。','muted'),toolbar,notice,creation,materialEditor,jobs,list,detail);

  function clear(){
    ++epoch;++detailEpoch;++materialEpoch;selectedId=null;loadedProfileId=null;materials=[];sourceInputs=[];
    for(const n of [toolbar,notice,creation,materialEditor,jobs,list,detail])n.replaceChildren();
    settings.replaceChildren();settings.hidden=true;
  }
  async function load(){
    const id=profile()?.id;
    if(!id){clear();return;}
    // Synchronously hide the previous person's content before any network wait.
    if(loadedProfileId!==id){clear();loadedProfileId=id;}
    const c={...capture(),epoch:++epoch};++detailEpoch;++materialEpoch;
    notice.textContent='正在加载章节…';detail.replaceChildren();materialEditor.replaceChildren();settings.replaceChildren();settings.hidden=true;
    toolbar.replaceChildren(button('刷新章节与任务',()=>load(),c));
    try{
      const requests=[api(profilePath(c)+'/chapters')];
      if(c.editable)requests.push(api(profilePath(c)+'/materials'),api(profilePath(c)+'/writing-jobs'));
      const [result,sourceResult,jobResult]=await Promise.all(requests);
      if(!valid(c))return;
      materials=sourceResult?.materials||[];
      renderCreation(c);renderJobs(c,Array.isArray(jobResult)?jobResult:jobResult?.jobs||result.jobs||[]);
      renderList(c,result.chapters||[]);notice.textContent='';
      if(selectedId)await showChapter(selectedId,c);
      if(valid(c)&&canConfigure())await loadModels(c);
    }catch(error){message(error.message,c,notice);}
  }
  function selectedSources(){return sourceInputs.filter(({input})=>input.checked).map(({material:m})=>({kind:m.kind,id:m.id}));}
  function renderCreation(c){
    creation.replaceChildren();sourceInputs=[];if(!c.editable)return;
    const sourceSection=section('选择素材（可多选）');
    sourceSection.append(el('p','可组合多次已结束聊天和导入资料。校订会另存版本，原始文字与录音保留。','muted'));
    const options=el('div','','chapter-materials');
    for(const m of materials){
      const row=el('div','','chapter-material');const label=el('label');const check=el('input');check.type='checkbox';
      label.append(check,el('span',`${m.mode==='listening'?'旁听':m.kind==='session'?'聊天':'导入'}：${m.title} · 素材版本 ${m.revision} · ${m.chars} 字${m.identity_confirmation_required?' · 请先核对主体声音':''}`));
      check.disabled=!!m.identity_confirmation_required;
      sourceInputs.push({input:check,material:m});
      row.append(label,button('校订：'+m.title,()=>editMaterial(m,c),c));options.append(row);
    }
    if(!materials.length)options.append(el('p','还没有可用素材。请先完成一次聊天或导入原文。','muted'));
    sourceSection.append(options);creation.append(sourceSection);
    const form=el('form','','','chapter-create-form');
    const title=field(form,'新章节标题','input','chapter-new-title');title.required=true;title.maxLength=200;
    const brief=field(form,'这一章想讲什么（可选）','textarea','chapter-new-brief');brief.rows=3;brief.maxLength=4000;
    const submit=el('button','创建并生成草稿');submit.type='submit';form.append(submit);
    form.onsubmit=async event=>{
      event.preventDefault();if(!valid(c)||submit.disabled)return;
      const sources=selectedSources();
      if(!title.value.trim()){message('请填写章节标题。',c,notice);return;}
      if(!sources.length){message('请至少选择一份素材，可同时选择多次聊天和导入资料。',c,notice);return;}
      submit.disabled=true;let created=false;
      try{
        const chapter=await api(profilePath(c)+'/chapters','POST',{title:title.value.trim(),brief:brief.value.trim()});
        if(!valid(c))return;selectedId=chapter.id;created=true;
        await api(chapterPath(chapter.id)+'/generate','POST',{sources,brief:brief.value.trim()});
        if(valid(c)){await load();if(profile()?.id===c.id)note('章节任务已提交。请手动刷新查看进度；已确认章节仍可阅读。');}
      }catch(error){
        if(valid(c)&&created){const text=error.message;await load();if(profile()?.id===c.id)note('章节已创建，生成未成功：'+text+'。可在该章节重新生成。');}
        else message(error.message,c,notice);
      }finally{if(valid(c))submit.disabled=false;}
    };
    creation.append(form);
  }
  async function editMaterial(material,c){
    if(material.mode==='listening'){await openSession(material.id);return;}
    const request=++materialEpoch;
    const path=profilePath(c)+`/materials/${pathId(material.kind)}/${pathId(material.id)}`;
    const data=await api(path);if(!valid(c)||request!==materialEpoch)return;
    materialEditor.replaceChildren();
    const heading=el('div','','chapter-toolbar');heading.append(el('h3','校订素材：'+data.title),button('收起校订',()=>{++materialEpoch;materialEditor.replaceChildren();},c));
    const original=el('details');original.append(el('summary','原始文字（保留不改）'),el('pre',data.original_text));
    const form=el('form','','','chapter-material-form');
    form.append(el('p',`当前素材版本 ${data.revision}。校订后自动提取事实；已有章节草稿可能过期，需要重新生成。`,'muted'));
    const text=field(form,'校订全文','textarea','chapter-material-text',data.text);text.rows=12;text.maxLength=100000;
    const status=el('p','','chapter-message');status.setAttribute('role','status');
    const save=el('button','保存素材校订');save.type='submit';form.append(save,status);
    form.onsubmit=async event=>{
      event.preventDefault();if(!valid(c)||request!==materialEpoch||save.disabled)return;save.disabled=true;
      try{
        await api(path,'PUT',{text:text.value,version:data.revision});
        if(!valid(c)||request!==materialEpoch)return;
        await load();if(profile()?.id===c.id)note('素材校订已保存。原声与原始文字未改；请检查章节草稿是否需要重新生成。');
      }catch(error){if(request===materialEpoch)message(error.message+'。版本或素材归属发生变化时，请刷新后重新校订。',c,status);}
      finally{if(valid(c)&&request===materialEpoch)save.disabled=false;}
    };
    materialEditor.append(heading,original,form);
  }
  function renderJobs(c,rows){
    jobs.replaceChildren();if(!c.editable)return;
    jobs.append(el('h3','后台任务'),el('p','进度不会自动刷新。完成或失败后，点击“刷新章节与任务”；确认版本不会被新任务覆盖。','muted'));
    const states={pending:'排队中',running:'处理中',completed:'已完成',failed:'失败'};
    for(const j of rows.slice(0,20)){
      const row=el('div','','chapter-job');
      const stage={extract:'提取事实',outline:'拟定提纲',write:'章节写作',audit:'事实核对',polish:'文字润色'}[j.stage]||'';
      row.append(el('p',`${j.title||'写作任务'}：${states[j.status]||j.status}${stage?' · '+stage:''}${j.total?' · '+(j.progress||0)+'/'+j.total:''}`));
      if(j.error)row.append(el('p',j.error,'muted'));
      if(j.status==='failed')row.append(button('重试任务',async()=>{await api('/life/writing-jobs/'+pathId(j.id)+'/retry','POST',{});if(valid(c))await load();},c));
      jobs.append(row);
    }
    if(!rows.length)jobs.append(el('p','暂无写作任务。','muted'));
  }
  function renderList(c,chapters){
    list.replaceChildren(el('h3','章节与历史版本'));
    for(const chapter of chapters){
      const state=chapter.published_version?`已确认第 ${chapter.published_version} 版`:'尚未确认';
      const draft=c.editable?` · 草稿 ${chapter.draft_count||0} 份${chapter.stale?' · 素材已变化':''}`:'';
      list.append(button(`${chapter.title} · ${state}${draft}`,()=>showChapter(chapter.id,c),c));
    }
    if(!chapters.length)list.append(el('p',c.editable?'还没有章节。选好素材，写下标题后生成第一份草稿。':'还没有已确认的章节。','muted'));
    if(selectedId&&!chapters.some(chapter=>chapter.id===selectedId))selectedId=null;
  }
  async function showChapter(id,c){
    if(!valid(c))return;selectedId=id;const request=++detailEpoch;
    detail.replaceChildren(el('p','正在加载版本…','muted'));
    const data=await api(chapterPath(id));if(!valid(c)||request!==detailEpoch)return;
    detail.replaceChildren();
    const versions=(data.versions||[]).filter(v=>c.editable||v.status==='confirmed');
    const chapter=data.chapter;const published=versions.find(v=>v.id===chapter.published_version_id)||versions.find(v=>v.status==='confirmed');
    const latestDraft=versions.filter(v=>v.status==='draft').sort((a,b)=>b.number-a.number)[0];
    detail.append(el('h3','章节版本对照'));
    if(c.editable)detail.append(button('用当前素材重新生成草稿',async()=>{
      let sources=selectedSources();
      if(!sources.length){
        const seen=new Set();sources=(latestDraft?.sources||published?.sources||[]).filter(s=>{const key=s.source_kind+':'+s.source_id;if(seen.has(key))return false;seen.add(key);return true;}).map(s=>({kind:s.source_kind,id:s.source_id}));
      }
      if(!sources.length)throw Error('请先在上方选择本档案的素材。');
      await api(chapterPath(id)+'/generate','POST',{sources});if(valid(c))await load();
    },c));
    const compare=el('div','','chapter-comparison');
    const accepted=section('当前已确认章节','chapter-version');
    if(published)renderReadOnly(accepted,published,c);else accepted.append(el('p','尚无已确认版本；生成和保存草稿不会自动入书。','muted'));
    compare.append(accepted);
    const chosen=section(c.editable?'草稿与历史版本':'已确认版本历史','chapter-version');
    const select=field(chosen,'查看版本','select','chapter-version-select');
    for(const v of versions){const option=el('option',`第 ${v.number} 版 · ${v.status==='confirmed'?'已确认':'草稿'}${v.stale?' · 素材已变化':''}`);option.value=v.id;select.append(option);}
    const content=el('div');chosen.append(content);
    const show=v=>{
      content.replaceChildren();if(!v)return;
      if(v.status==='draft'&&c.editable)renderDraft(content,id,v,c,request);else renderReadOnly(content,v,c);
      renderProvenance(content,v,c);
    };
    select.value=(latestDraft||published||versions[0])?.id||'';
    select.onchange=()=>{if(valid(c)&&request===detailEpoch)show(versions.find(v=>v.id===select.value));};
    show(versions.find(v=>v.id===select.value));
    if(!versions.length)content.append(el('p','任务完成后，草稿将在这里出现。','muted'));
    compare.append(chosen);detail.append(compare);
  }
  function renderReadOnly(parent,v,c){
    parent.append(el('small',`第 ${v.number} 版 · ${v.status==='confirmed'?'已确认（只读）':'草稿'}`),el('h4',v.title),el('div',v.body,'book-body'));
    if(v.status==='confirmed')parent.append(el('p','新的草稿不会改写这一版；只有明确确认新版本才会更新当前章节。','muted'));
  }
  function renderDraft(parent,chapterId,v,c,request){
    const form=el('form','','','chapter-edit-form');
    form.append(el('p',v.stale?'素材已经校订，这份草稿已过期，不能确认。请用当前素材重新生成。':'这是 AI 草稿。请逐一核实事实与引用，保存修改后再明确确认入书。',v.stale?'chapter-warning':'muted'));
    const title=field(form,'草稿标题','input','chapter-draft-title',v.title);title.required=true;title.maxLength=200;
    const body=field(form,'草稿正文','textarea','chapter-draft-body',v.body);body.rows=16;body.maxLength=30000;
    const status=el('p','','chapter-message');status.setAttribute('role','status');
    const save=el('button','保存草稿修改');save.type='submit';
    const confirm=button('确认此版本收入人生之书',async()=>{
      if(request!==detailEpoch||v.stale||title.value!==v.title||body.value!==v.body)return;
      try{await api(chapterPath(chapterId)+`/versions/${pathId(v.id)}/confirm`,'POST',{edit_version:v.edit_version});if(valid(c))await load();}
      catch(error){message(error.message+'。若素材或已确认版本发生变化，请刷新并核对后重新生成。',c,status);}
    },c,{id:'chapter-confirm'});
    confirm.disabled=!!v.stale;
    const dirty=()=>{confirm.disabled=!!v.stale||title.value!==v.title||body.value!==v.body;status.textContent=confirm.disabled&&!v.stale?'修改尚未保存。请先保存草稿，再确认入书。':'';};
    title.oninput=body.oninput=dirty;
    form.onsubmit=async event=>{
      event.preventDefault();if(!valid(c)||request!==detailEpoch||save.disabled)return;save.disabled=true;confirm.disabled=true;let failed=false;
      try{
        await api(chapterPath(chapterId)+`/versions/${pathId(v.id)}`,'PUT',{title:title.value,body:body.value,edit_version:v.edit_version});
        if(valid(c)&&request===detailEpoch)await load();
      }catch(error){failed=true;message(error.message+'。如版本已变化，请刷新后重新核对。',c,status);}
      finally{if(valid(c)&&request===detailEpoch){save.disabled=false;if(!failed)dirty();}}
    };
    form.append(save,confirm,status);parent.append(form);
  }
  function renderProvenance(parent,v,c){
    if(v.metadata?.human_edited)parent.append(el('p','正文经过人工修改。以下引用保留素材来源，不再声称与新正文逐段对齐；请在确认前逐项核实。','muted'));
    const sources=v.sources||[];
    const evidence=[...(v.evidence||[])];
    if(!evidence.length)for(const [index,p] of (v.paragraphs||[]).entries())for(const citation of p.citations||[])evidence.push({...citation,paragraph:index+1});
    const refs=el('details');refs.append(el('summary','引用依据与原声（仍需人工核实）'));
    for(const citation of evidence){
      const source={...sources.find(s=>s.unit_id===citation.unit_id),...citation};
      const row=el('div','','evidence');
      if(source.speaker)row.append(el('p',`${source.speaker.confirmed?source.speaker.name:'身份未确认'}${source.speaker.relationship?' · '+source.speaker.relationship:''}${source.speaker.is_subject?' · 档案主体':' · 其他或未知说话者'}`,'muted'));
      row.append(el('small',`${source.title||'素材'} · 素材版本 ${source.revision??'未知'}${source.paragraph?' · 第 '+source.paragraph+' 段':''}`),el('p','“'+(source.quote||'')+'”'));
      if(source.source_kind==='session'&&source.source_id){
        const aligned=Number.isFinite(source.seconds);
        row.append(el('p',aligned?`${source.segment_id&&source.origin==='human_revision'?'分句校订（对应原声片段）':'原始识别'}${source.turn_id?' #'+source.turn_id:''} · 约 ${Math.floor(source.seconds)} 秒`:'校订全文引用，无法逐句定位；仅可回听整段录音。','muted'));
        row.append(button(aligned?`回听约 ${Math.floor(source.seconds)} 秒处`:'回听整段录音（无逐句定位）',()=>openSession(source.source_id,source.seconds??0),c));
      }else row.append(el('p','导入资料，无录音。','muted'));
      refs.append(row);
    }
    if(!evidence.length)refs.append(el('p','该版本没有可展示的逐段引用，请结合素材快照核实。','muted'));
    const snapshots=el('details');snapshots.append(el('summary','本版本采用的素材快照'));
    snapshots.append(el('p','这里保留生成当时的素材版本。后续校订不会覆盖这些依据。','muted'));
    for(const s of sources){
      const snap=el('details');snap.append(el('summary',`${s.title||'素材'} · ${s.source_kind==='session'?'聊天':'导入'} · 版本 ${s.revision}`),el('pre',s.text||''));
      if(s.speaker)snap.append(el('p',`说话者：${s.speaker.name} · ${s.speaker.is_subject?'档案主体':'其他或未知说话者'}`,'muted'));
      if(s.original_text!==undefined&&s.original_text!==s.text)snap.append(el('p','初始识别（保留）：'+s.original_text,'muted'));
      if(s.context)snap.append(el('p',typeof s.context==='string'?s.context:JSON.stringify(s.context),'muted'));
      snapshots.append(snap);
    }
    parent.append(refs,snapshots);
    if(v.metadata&&typeof v.metadata==='object'){
      const meta=el('details');meta.append(el('summary','生成信息'),el('pre',JSON.stringify(v.metadata,null,2)));parent.append(meta);
    }
  }
  async function loadModels(c){
    const config=await api('/life/editor-models');if(!valid(c)||!canConfigure()||!config.can_configure)return;
    settings.replaceChildren();settings.hidden=false;
    const disclosure=el('details');disclosure.append(el('summary','管理员 · 素材整理与章节写作模型'));
    disclosure.append(el('p','素材提取和章节写作分别配置。默认使用已有豆包文本调用（模型 1.2.1.1）；配置保存不代表已验证连接。密钥只存在服务器，不回显；同一供应商留空保留已存密钥。','muted'));
    const form=el('form','','','editor-model-form'),inputs={};
    for(const [role,label] of [['extractor','素材整理'],['writer','章节写作']]){
      const group=section(label),saved=config[role]||{};
      const provider=field(group,'供应商','select',role+'-provider');
      for(const [value,name] of [['doubao_realtime','豆包实时服务的文本调用'],['deepseek','DeepSeek'],['ark','火山方舟']]){const o=el('option',name);o.value=value;provider.append(o);}
      provider.value=saved.provider||'doubao_realtime';
      const model=field(group,'模型名称','input',role+'-model',saved.model||'1.2.1.1');model.required=true;model.maxLength=200;
      const key=field(group,'API Key（留空保留同供应商已存密钥）','input',role+'-api-key','','password');key.autocomplete='off';
      const status=el('p',saved.configured?'服务器报告已配置；尚未在此验证调用。':'服务器报告尚未配置可用凭据。','muted');
      provider.onchange=()=>{key.value='';status.textContent=provider.value===(saved.provider||'doubao_realtime')?(saved.configured?'服务器报告该供应商已配置；尚未验证调用。':'该供应商尚未配置可用凭据。'):'已切换供应商。请填写此供应商的模型与凭据；旧供应商密钥不会自动沿用。';};
      group.append(status);form.append(group);inputs[role]={provider,model,key};
    }
    const save=el('button','保存两组模型配置');save.type='submit';const status=el('p','','chapter-message');status.setAttribute('role','status');form.append(save,status);
    form.onsubmit=async event=>{
      event.preventDefault();if(!valid(c)||!canConfigure()||save.disabled)return;save.disabled=true;
      const payload={};for(const [role,{provider,model,key}] of Object.entries(inputs)){payload[role]={provider:provider.value,model:model.value.trim()};if(key.value.trim())payload[role].api_key=key.value.trim();}
      try{await api('/life/editor-models','PUT',payload);if(valid(c)&&canConfigure()){await loadModels(c);if(valid(c))note('模型配置已保存。实际生成时才会验证供应商调用。');}}
      catch(error){message(error.message,c,status);}
      finally{for(const {key} of Object.values(inputs))key.value='';if(valid(c))save.disabled=false;}
    };
    disclosure.append(form);settings.append(disclosure);
  }
  return {load,clear};
}
