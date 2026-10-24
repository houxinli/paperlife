const BASE=typeof location==='undefined'?'':location.pathname?.split('/life')[0]||'';
export class VoiceCall {
  constructor(onEvent){
    this.onEvent=onEvent;this.sources=new Set();this.next=0;this.closed=false;this.suppressed=false;
    this.cancelledReplies=new Set();this.cancelledQuestions=new Set();this.finishedReplies=new Set();
    this.diagnostics={receivedChunks:0,receivedBytes:0,scheduledChunks:0,suppressedChunks:0,audioStarts:0,staleStarts:0,resumeAttempts:0,resumeFailures:0};
  }
  async prepare(){
    this.context=new AudioContext();
    await this.context.resume();
    this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(this.closed){this.stream.getTracks().forEach(t=>t.stop());return;}
    await this.context.audioWorklet.addModule(BASE+'/life/capture.js');
    this.input=this.context.createMediaStreamSource(this.stream);
    this.worklet=new AudioWorkletNode(this.context,'life-capture');
    this.mute=this.context.createGain();this.mute.gain.value=0;
    this.input.connect(this.worklet);this.worklet.connect(this.mute);this.mute.connect(this.context.destination);
    this.worklet.port.onmessage=({data})=>{
      if(this.ready&&this.ws?.readyState===WebSocket.OPEN){
        if(this.ws.bufferedAmount>128000){this.onEvent({type:'error',message:'网络上传跟不上，采访已暂停。请换稳定网络后继续。'});this.dispose();return;}
        this.ws.send(data);
      }
    };
  }
  connect(ticket){
    if(this.context)this.context.onstatechange=()=>{
      if(this.closed||this.stopping)return;
      if(this.context.state==='running'){
        this.resumeBlocked=false;this.onEvent({type:'audio_state',state:this.sources.size?'playing':'ready'});
      }else if(this.context.state!=='closed'&&!this.resuming&&!this.resumeBlocked)void this.resumeAudio();
    };
    this.ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${BASE}/api/life/live`);
    this.ws.binaryType='arraybuffer';
    this.timer=setTimeout(()=>{this.onEvent({type:'error',message:'连接超时，请检查语音服务配置。'});this.dispose();},40000);
    this.ws.onopen=()=>this.ws.send(JSON.stringify({ticket}));
    this.ws.onmessage=({data})=>{
      if(this.closed)return;
      if(data instanceof ArrayBuffer){
        this.diagnostics.receivedChunks++;this.diagnostics.receivedBytes+=data.byteLength;
        if(!this.stopping&&!this.suppressed)this.play(data);else this.diagnostics.suppressedChunks++;
        return;
      }
      const event=JSON.parse(data);
      if(event.type==='ready'){clearTimeout(this.timer);if(!this.stopping)this.ready=true;}
      if(event.type==='interrupt'){this.stopAnswer();this.questionId=event.question_id||null;}
      if(event.type==='audio_start')this.startAudio(event);
      if(event.type==='answer_start'&&!this.staleReply(event)){
        this.replyId=event.reply_id||null;
        // 兼容尚未发出独立音频边界的旧服务器；文本流不能打开新版音频门。
        if(!this.audioBoundariesSeen&&event.source!=='text')this.suppressed=false;
      }
      if(event.type==='audio_end'){
        if(event.reply_id)this.finishedReplies.add(event.reply_id);
        if((!event.reply_id||event.reply_id===this.audioReplyId)&&(!event.question_id||event.question_id===this.audioQuestionId))this.suppressed=true;
        this.reportAudio('audio_end');
      }
      if(event.type==='ending')this.beginEnding();
      if(event.type==='ended'){this.ended=true;this.dispose();}
      this.onEvent(event);
    };
    this.ws.onerror=()=>this.onEvent({type:'error',message:'网络连接失败，请稍后重试。'});
    this.ws.onclose=()=>{this.dispose();if(!this.ended)this.onEvent({type:'disconnected'});};
  }
  staleReply(event){
    return (event.reply_id&&this.cancelledReplies.has(event.reply_id))||
      (event.question_id&&(this.cancelledQuestions.has(event.question_id)||(this.questionId&&event.question_id!==this.questionId)));
  }
  startAudio(event){
    this.audioBoundariesSeen=true;this.diagnostics.audioStarts++;
    this.audioReplyId=event.reply_id||null;this.audioQuestionId=event.question_id||null;
    this.suppressed=Boolean(this.staleReply(event)||(event.reply_id&&this.finishedReplies.has(event.reply_id)));
    if(this.suppressed)this.diagnostics.staleStarts++;
    else{this.replyId=event.reply_id||null;this.resumeBlocked=false;}
    this.reportAudio('audio_start');
  }
  reportAudio(reason){
    this.onEvent({type:'audio_diagnostic',reason,...this.diagnostics,contextState:this.context?.state||'unavailable',queuedSources:this.sources.size,suppressed:this.suppressed});
  }
  async resumeAudio(){
    if(this.closed||this.stopping||!this.context||this.context.state==='closed')return false;
    if(this.context.state==='running'){
      this.resumeBlocked=false;this.onEvent({type:'audio_state',state:this.sources.size?'playing':'ready'});return true;
    }
    this.diagnostics.resumeAttempts++;
    // 某些手机会把自动 resume 一直挂起；立即显示恢复入口，点击后可再次请求。
    this.onEvent({type:'audio_state',state:'blocked',message:'声音被浏览器暂停了，请点“恢复声音”。'});
    const attempt=(async()=>{
      try{
        await this.context.resume();
        if(this.closed||this.stopping)return false;
        if(this.context.state!=='running')throw new Error('Audio context is not running');
        this.resumeBlocked=false;
        this.onEvent({type:'audio_state',state:this.sources.size?'playing':'ready'});
        this.reportAudio('resumed');return true;
      }catch{
        if(this.closed||this.stopping)return false;
        if(this.context.state==='running')return true;
        this.resumeBlocked=true;this.diagnostics.resumeFailures++;
        this.onEvent({type:'audio_state',state:'blocked',message:'声音被浏览器暂停了，请点“恢复声音”。'});
        this.reportAudio('resume_blocked');return false;
      }
    })();
    this.resuming=attempt;
    void attempt.finally(()=>{if(this.resuming===attempt)this.resuming=null;});
    return attempt;
  }
  play(data){
    if(this.closed)return;
    if(this.context.state!=='running'&&!this.resumeBlocked&&!this.resuming)void this.resumeAudio();
    const samples=new Int16Array(data),buffer=this.context.createBuffer(1,samples.length,24000),values=buffer.getChannelData(0);
    for(let i=0;i<samples.length;i++)values[i]=samples[i]/32768;
    const source=this.context.createBufferSource();source.buffer=buffer;source.connect(this.context.destination);
    this.next=Math.max(this.next,this.context.currentTime+.035);
    source.start(this.next);this.next+=buffer.duration;this.sources.add(source);this.diagnostics.scheduledChunks++;
    if(this.sources.size===1&&this.context.state==='running')this.onEvent({type:'audio_state',state:'playing'});
    source.onended=()=>{this.sources.delete(source);if(!this.sources.size&&!this.closed&&this.context.state==='running')this.onEvent({type:'audio_state',state:'ready'});};
  }
  interrupt(){for(const source of this.sources){try{source.stop();}catch{}}this.sources.clear();this.next=0;}
  stopAnswer(){
    if(this.replyId)this.cancelledReplies.add(this.replyId);
    if(this.audioReplyId)this.cancelledReplies.add(this.audioReplyId);
    if(this.audioQuestionId)this.cancelledQuestions.add(this.audioQuestionId);
    this.suppressed=true;this.interrupt();this.reportAudio('interrupt');
  }
  beginEnding(){
    if(this.closed||this.stopping)return false;
    this.stopping=true;this.ready=false;this.stream?.getTracks().forEach(t=>t.stop());this.interrupt();
    this.stopTimer=setTimeout(()=>this.dispose(),8000);
    return true;
  }
  stop(){
    if(!this.beginEnding())return;
    if(this.ws?.readyState===WebSocket.OPEN)this.ws.send(JSON.stringify({type:'stop'}));
    else this.dispose();
  }
  dispose(){
    if(this.closed)return;this.closed=true;this.ready=false;clearTimeout(this.timer);clearTimeout(this.stopTimer);
    this.stream?.getTracks().forEach(t=>t.stop());this.interrupt();this.input?.disconnect();this.worklet?.disconnect();this.mute?.disconnect();
    if(this.context)this.context.onstatechange=null;
    this.reportAudio('closed');
    this.context?.close().catch(()=>{});this.ws?.close();
  }
}
