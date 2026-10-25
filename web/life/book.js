/* 原始资料、结构化事实、文学性故事三层分别保存。 */
export function createBook({api,profile,openSession,note}){
  const $=id=>document.getElementById(id);
  let items=[],sources=[],sessions=[],current=null,layer='review',generation=0;
  const kinds={fact:'事实资料',person:'人物',event:'时间线事件',story:'故事篇章',question:'待确认问题'};
  const states={draft:'草稿',confirmed:'已确认',open:'待回答',resolved:'已解决',archived:'已归档'};
  const el=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
  const click=(text,fn)=>{const b=el('button',text,'secondary');b.type='button';b.onclick=async()=>{try{await fn();}catch(e){note(e.message);}};return b;};
  function showSource(title,text){$('source-title').textContent=title;$('source-text').textContent=text;$('source-view').showModal();}
  async function load(){
    const id=profile().id,epoch=++generation;
    const result=await Promise.all([api(`/life/profiles/${id}/items`),api(`/life/profiles/${id}/sources`),api(`/life/profiles/${id}/sessions`)]);
    if(profile().id!==id||epoch!==generation)return;
    [items,sources,sessions]=result;
    $('book-title').textContent=profile().name+'的人生之书';
    $('book-tools').hidden=profile().role==='reader';
    document.querySelector('[data-layer=review]').hidden=profile().role==='reader';
    if(profile().role==='reader'&&layer==='review')layer='facts';
    $('review-count').textContent=items.filter(i=>i.status==='draft').length;
    $('question-count').textContent=items.filter(i=>i.kind==='question'&&i.status==='open').length;
    render();await jobs();
  }
  function render(){
    const root=$('book-content');root.replaceChildren();
    if(layer==='sources'){
      root.append(el('p','这里保存导入原文、录音和原始转写。校对与润色另存，不会覆盖它们。','muted'));
      for(const s of sources)root.append(click(s.title,()=>showSource(s.title,s.text)));
      for(const s of sessions)root.append(click('聊天：'+s.title,()=>openSession(s.id)));
      if(!sources.length&&!sessions.length)root.append(el('p','还没有原始资料，第一次聊天会从空白开始。'));
      return;
    }
    const visible=items.filter(i=>layer==='review'?i.status==='draft':layer==='facts'?['fact','person','event'].includes(i.kind)&&i.status==='confirmed':layer==='story'?i.kind==='story'&&i.status==='confirmed':i.kind==='question'&&['open','resolved'].includes(i.status));
    if(!visible.length)root.append(el('p',layer==='review'?'暂无待审核草稿。聊天结束后会自动整理，也可在历史聊天中手动发起。':layer==='question'?'暂时没有待确认问题。AI提议的问题需在待审核区确认后进入这里。':'这里只展示你确认过的内容。请先到待审核区校对确认。','muted'));
    for(const i of visible){
      const card=el('article','','book-card');card.append(el('small',kinds[i.kind]+' · '+states[i.status]),el('h3',i.title));
      if(i.stale)card.append(el('p',i.status==='draft'?'素材已校订：请审核新整理条目，这份旧草稿不能直接确认。':'来源有新的校订；这是此前已确认的内容。','muted'));
      const meta=[i.time_text,i.people,i.place].filter(Boolean).join(' · ');if(meta)card.append(el('p',meta,'muted'));
      card.append(el('p',i.body,'book-body'));if(i.answer)card.append(el('p','家人补充：'+i.answer,'book-answer'));
      const refs=el('div','','source-links');for(const id of i.source_ids){const s=sources.find(s=>s.id===id);const session=sessions.find(s=>s.id===id);if(s)refs.append(click('原文：'+s.title,()=>showSource(s.title,s.text)));if(session)refs.append(click('回听：'+session.title,()=>openSession(id)));}card.append(refs);
      for(const evidence of i.evidence||[]){
        const source=el('div','','evidence');const label=evidence.revision?`人工校订版本 ${evidence.revision}`:evidence.source_kind==='source'?'导入原文':`原始转写 #${evidence.turn_id}`;
        source.append(el('p',`${label}${evidence.paragraph?' · 故事第'+evidence.paragraph+'段':''}：“${evidence.quote}”`));
        if(evidence.session_id)source.append(click(evidence.seconds==null?'回听原声（校订稿无法逐句定位）':`回听约 ${Math.floor(evidence.seconds)} 秒处`,()=>openSession(evidence.session_id,Math.max(0,(evidence.seconds||0)-2))));
        const snap=(i.source_snapshot||[]).find(s=>s.unit_id===evidence.unit_id);if(snap)source.append(click('查看生成时的素材版本',()=>showSource(`${snap.title} · 版本 ${snap.revision}`,snap.text)));
        card.append(source);
      }
      if(i.generated_job)card.append(el('small','AI生成 · '+i.model+' · 来源已定位，内容仍需人工核实'));
      if(profile().role!=='reader')card.append(click(i.kind==='question'?'回答／补充':'编辑',()=>edit(i)));
      root.append(card);
    }
  }
  function edit(item){
    current={...item};$('item-heading').textContent=kinds[item.kind];$('item-message').textContent=item.stale&&item.status==='draft'?'素材已校订，请使用新整理条目。旧草稿可保留或归档。':'';
    $('confirm-item').disabled=!!item.stale&&item.status==='draft';
    for(const [id,key] of [['item-title','title'],['item-body','body'],['item-answer','answer'],['item-time','time_text'],['item-people','people'],['item-place','place']])$(id).value=item[key]||'';
    $('item-certainty').value=item.certainty||'unverified';$('structured-fields').hidden=!['fact','person','event'].includes(item.kind);$('answer-label').hidden=item.kind!=='question';
    $('item-status').replaceChildren();for(const state of item.kind==='question'?['draft','open','resolved','archived']:['draft','confirmed','archived']){const o=el('option',states[state]);o.value=state;$('item-status').append(o);}$('item-status').value=item.status||(item.kind==='question'?'open':'draft');
    $('item-sources').replaceChildren();for(const s of [...sources,...sessions]){const o=el('option',s.title);o.value=s.id;o.selected=(item.source_ids||[]).includes(s.id);$('item-sources').append(o);}
    $('confirm-item').textContent=item.kind==='question'?'确认问题／答案':'确认收入人生之书';
    $('item-history').hidden=!item.id;$('item-editor').showModal();
  }
  function newItem(kind,sourceId,body=''){edit({kind,title:'',body,source_ids:sourceId?[sourceId]:[],version:0});}
  $('item-form').onsubmit=async event=>{
    event.preventDefault();$('save-item').disabled=true;
    try{
      const data={kind:current.kind,title:$('item-title').value,body:$('item-body').value,answer:$('item-answer').value,status:$('item-status').value,time_text:$('item-time').value,people:$('item-people').value,place:$('item-place').value,certainty:$('item-certainty').value,source_ids:[...$('item-sources').selectedOptions].map(o=>o.value),version:current.version};
      await api(current.id?'/life/items/'+current.id:`/life/profiles/${profile().id}/items`,current.id?'PUT':'POST',data);
      $('item-editor').close();await load();
    }catch(e){$('item-message').textContent=e.message;}finally{$('save-item').disabled=false;}
  };
  $('confirm-item').onclick=()=>{$('item-status').value=current.kind==='question'?($('item-answer').value.trim()?'resolved':'open'):'confirmed';$('item-form').requestSubmit();};
  $('item-history').onclick=async()=>{try{const list=await api('/life/items/'+current.id+'/revisions');showSource('修改历史',list.map(r=>`版本 ${r.version} · ${r.created_at} UTC\n${r.snapshot.title}\n${r.snapshot.body}\n回答：${r.snapshot.answer}`).join('\n\n')||'尚无历史修改。');}catch(e){$('item-message').textContent=e.message;}};
  $('close-item').onclick=()=>$('item-editor').close();$('close-source').onclick=()=>$('source-view').close();
  document.querySelectorAll('[data-layer]').forEach(b=>b.onclick=()=>{layer=b.dataset.layer;render();});
  document.querySelectorAll('[data-new]').forEach(b=>b.onclick=()=>newItem(b.dataset.new));
  async function jobs(){
    if(profile().role==='reader'){$('processing-status').replaceChildren();return;}
    const id=profile().id;const jobs=await api(`/life/profiles/${id}/processing`);if(profile().id!==id)return;
    $('processing-status').replaceChildren();
    for(const j of jobs.slice(0,8)){
      const state={pending:'排队／等待重试',running:'正在自动整理',completed:'草稿已生成',failed:'整理失败'}[j.status]||j.status;
      const title=sessions.find(s=>s.id===j.session)?.title||'聊天';
      const row=el('div',`${title}：${state}${j.total?'（'+j.progress+'/'+j.total+'）':''}`);
      if(j.error&&j.status!=='completed')row.append(el('p',j.error,'muted'));
      if(j.status==='failed'&&profile().role!=='reader')row.append(click('重试',async()=>{await api('/life/sessions/'+j.session+'/process','POST',{});await load();}));
      $('processing-status').append(row);
    }
  }
  return {load,newItem,showSource};
}
