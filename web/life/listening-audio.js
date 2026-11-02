import {VoiceCall} from './audio.js';
const BASE=typeof location==='undefined'?'':location.pathname?.split('/life')[0]||'';
// Capture is shared with voice chat; the receive path deliberately has no audio player.
// 一次旁听的麦克风可以跨多段连接：某一段到时长上限或断线后，页面用新票据接着连，不重新申请麦克风。
export class ListeningCall extends VoiceCall {
  audioConstraints(){
    // 旁听不播放声音，回声消除没有参照；降噪和自动增益会压低外放里另一端的声音，原声交给识别处理。
    return {channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false};
  }
  async prepare(){
    await super.prepare();
    if(this.stopping||this.closed){this.stream?.getTracks().forEach(t=>t.stop());return;}
    this.context.onstatechange=()=>{
      if(!this.closed&&!this.stopping&&this.context.state!=='running'){
        this.onEvent({type:'notice',message:'页面或麦克风已暂停，正在保留已收到的录音。'});this.stop();
      }
    };
  }
  connect(ticket){
    if(this.closed||this.stopping)return;
    this.closePart();
    const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${BASE}/api/life/live`);
    this.ws=ws;this.ended=false;ws.binaryType='arraybuffer';
    this.timer=setTimeout(()=>{if(this.ws!==ws)return;this.onEvent({type:'error',message:'旁听连接超时。'});this.partLost();},40000);
    ws.onopen=()=>{if(this.ws===ws&&!this.closed)ws.send(JSON.stringify({ticket}));};
    ws.onmessage=({data})=>{
      if(this.ws!==ws||this.closed||typeof data!=='string')return;
      let event;try{event=JSON.parse(data);}catch{return;}
      if(!['ready','listening_transcript','saved','ending','ended','error','notice'].includes(event.type))return;
      if(event.type==='ready'){clearTimeout(this.timer);if(!this.stopping)this.ready=true;}
      // 服务器主动收尾（到时长上限）只结束这一段；麦克风继续，等下一段票据。
      if(event.type==='ending'&&!this.stopping)this.ready=false;
      if(event.type==='ended'){this.ended=true;if(this.stopping)this.dispose();else this.closePart();}
      this.onEvent(event);
    };
    ws.onerror=()=>{if(this.ws===ws)this.onEvent({type:'error',message:'旁听连接失败；已收到的录音会保留。'});};
    ws.onclose=()=>{
      if(this.ws!==ws)return;
      const lost=!this.ended;
      if(this.stopping)this.dispose();else this.closePart();
      if(lost)this.onEvent({type:'disconnected'});
    };
  }
  // 只结束当前这一段连接；不停麦克风。
  closePart(){
    const ws=this.ws;this.ws=null;this.ready=false;clearTimeout(this.timer);
    if(!ws)return;
    ws.onopen=ws.onmessage=ws.onerror=ws.onclose=null;
    try{ws.close();}catch{}
  }
  partLost(){this.closePart();this.onEvent({type:'disconnected'});}
  maxBuffered(){return 640000;} // 旁听不要求实时，容忍约 20 秒上传积压再换一段，减少分段。
  uploadStalled(){this.onEvent({type:'notice',message:'网络上传跟不上，正在重新连接…'});this.partLost();}
  play(){} // Defence in depth: never create an output source, even when called directly.
  beginEnding(){
    if(!super.beginEnding())return false;
    clearTimeout(this.timer);clearTimeout(this.stopTimer);
    this.stopTimer=setTimeout(()=>{
      this.onEvent({type:'notice',message:'最终识别仍未确认，已收到的录音会保留，请刷新档案查看。'});
      this.dispose();this.onEvent({type:'disconnected'});
    },45000);
    return true;
  }
  dispose(){
    const ws=this.ws;
    if(ws){ws.onopen=ws.onmessage=ws.onerror=ws.onclose=null;}
    super.dispose();
  }
}
