/* 讲述者自己的只读历史与已确认成果；不复用家人的编辑表单。 */
export function createNarrator({api,profile,isGuest,fetchAudio,note}){
  const $=id=>document.getElementById(id);
  let offset=0,epoch=0,detailEpoch=0,audioUrl=null;
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,fn)=>{const b=el('button',text,'secondary');b.type='button';b.onclick=async()=>{try{await fn();}catch(e){note(e.message);}};return b;};
  const base=()=>isGuest()?'/life/guest':`/life/profiles/${profile().id}`;
  const detailPath=id=>isGuest()?`/life/guest/history/${id}`:`/life/sessions/${id}`;
  async function history(reset=true){
    if(!profile())return;const id=profile().id,generation=reset?++epoch:epoch;
    if(reset){offset=0;$('past-sessions').replaceChildren();}
    const page=await api(base()+`/history?offset=${offset}&limit=20`);
    if(id!==profile()?.id||generation!==epoch)return;
    for(const s of page.sessions){
      const date=new Date(s.created_at.replace(' ','T')+'Z');const label=Number.isNaN(date.getTime())?s.created_at:date.toLocaleString('zh-CN',{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
      const row=button(`${s.title} · ${label}`,()=>openSession(s.id));row.className='history-entry';$('past-sessions').append(row);
    }
    offset+=page.sessions.length;$('more-history').hidden=!page.has_more;
    if(!offset)$('past-sessions').append(el('p','第一次聊天，就从这里开始。','muted'));
  }
  async function openSession(id,seconds=0){
    const generation=++detailEpoch,currentProfile=profile().id;const s=await api(detailPath(id));if(profile().id!==currentProfile||generation!==detailEpoch)return;
    $('past-title').textContent=s.title;$('past-turns').replaceChildren();
    for(const t of s.turns){const row=el('div','',`turn ${t.role}`);row.append(el('b',t.role==='user'?(profile().narrator_name||profile().name):(profile().interviewer_name||'拾光')+' · AI'),el('p',t.text));$('past-turns').append(row);}
    $('past-audio').pause();$('past-audio').hidden=true;
    $('past-play').onclick=async()=>{try{
      const data=await fetchAudio(detailPath(id)+'/audio?track=user');
      if(currentProfile!==profile()?.id||generation!==detailEpoch)return;
      if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(data);
      const player=$('past-audio');player.src=audioUrl;player.hidden=false;
      player.onloadedmetadata=()=>{player.currentTime=Math.max(0,seconds);};await player.play();
    }catch(e){note(e.message);}};
    // 弹窗阅读不更改当前聊天状态；音轨播放明确由用户点选。
    $('past-session').showModal();
  }
  async function book(){
    if(!profile())return;const id=profile().id;const data=await api(base()+'/book');if(profile().id!==id)return;
    const root=$('my-book-content');root.replaceChildren();
    $('my-book-status').textContent=data.pending_count?`还有 ${data.pending_count} 条内容等家人校对，确认后会放到这里。`:'这里保存家人校对确认后的内容。';
    if(!data.items.length){root.append(el('p','您的故事正在慢慢成书。聊完以后，家人会帮忙整理。','book-empty'));return;}
    const labels={fact:'生活片段',person:'故事里的人',event:'人生时间线',story:'故事篇章'};
    for(const group of ['person','event','fact','story']){
      const items=data.items.filter(i=>i.kind===group);if(!items.length)continue;
      const section=el('section','','my-book-section');section.append(el('h3',labels[group]));
      for(const item of items){const article=el('article','','book-card');article.append(el('h4',item.title));
        const meta=[item.time_text,item.people,item.place].filter(Boolean).join(' · ');if(meta)article.append(el('p',meta,'muted'));
        article.append(el('p',item.body,'book-body'));
        if(item.evidence?.length){const refs=el('details');refs.append(el('summary','这段内容从哪里来'));
          for(const e of item.evidence){refs.append(el('p',`原话：“${e.quote}”`),button('看看这次聊天',()=>openSession(e.session_id,Math.max(0,e.seconds-2))));}article.append(refs);}
        section.append(article);
      }root.append(section);
    }
  }
  $('more-history').onclick=()=>history(false).catch(e=>note(e.message));
  $('past-chats').addEventListener('toggle',()=>{if($('past-chats').open)history().catch(e=>note(e.message));});
  $('close-past').onclick=()=>$('past-session').close();
  $('past-session').addEventListener('close',()=>{detailEpoch++;$('past-audio').pause();if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=null;});
  function reset(){epoch++;detailEpoch++;offset=0;$('past-session').close();$('past-audio').pause();$('past-sessions').replaceChildren();$('my-book-content').replaceChildren();$('past-chats').open=false;}
  return {history,book,openSession,reset};
}
