import {VoiceCall} from './audio.js';
const BASE=typeof location==='undefined'?'':location.pathname?.split('/life')[0]||'';
// Capture is shared with voice chat; the receive path deliberately has no audio player.
export class ListeningCall extends VoiceCall {
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
    this.ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${BASE}/api/life/live`);
    this.ws.binaryType='arraybuffer';
    this.timer=setTimeout(()=>{this.onEvent({type:'error',message:'旁听连接超时，请检查服务配置。'});this.dispose();},40000);
    this.ws.onopen=()=>{if(!this.closed)this.ws.send(JSON.stringify({ticket}));};
    this.ws.onmessage=({data})=>{
      if(this.closed||typeof data!=='string')return;
      let event;try{event=JSON.parse(data);}catch{return;}
      if(!['ready','listening_transcript','saved','ending','ended','error','notice'].includes(event.type))return;
      if(event.type==='ready'){clearTimeout(this.timer);if(!this.stopping)this.ready=true;}
      if(event.type==='ending')this.beginEnding();
      if(event.type==='ended'){this.ended=true;this.dispose();}
      this.onEvent(event);
    };
    this.ws.onerror=()=>{this.onEvent({type:'error',message:'旁听连接失败；已收到的录音会保留，请稍后查看档案。'});this.stop();};
    this.ws.onclose=()=>{this.dispose();if(!this.ended)this.onEvent({type:'disconnected'});};
  }
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
}
