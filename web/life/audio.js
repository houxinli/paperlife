const BASE=typeof location==='undefined'?'':location.pathname?.split('/life')[0]||'';
export class VoiceCall {
  constructor(onEvent){this.onEvent=onEvent;this.sources=new Set();this.next=0;this.closed=false;}
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
    this.ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${BASE}/api/life/live`);
    this.ws.binaryType='arraybuffer';
    this.timer=setTimeout(()=>{this.onEvent({type:'error',message:'连接超时，请检查语音服务配置。'});this.dispose();},40000);
    this.ws.onopen=()=>this.ws.send(JSON.stringify({ticket}));
    this.ws.onmessage=({data})=>{
      if(data instanceof ArrayBuffer){if(!this.stopping&&!this.suppressed)this.play(data);return;}
      const event=JSON.parse(data);
      if(event.type==='ready'){clearTimeout(this.timer);this.ready=true;}
      if(event.type==='interrupt'){this.suppressed=true;this.interrupt();}
      if(event.type==='answer_start')this.suppressed=false;
      if(event.type==='ended'){this.ended=true;this.dispose();}
      this.onEvent(event);
    };
    this.ws.onerror=()=>this.onEvent({type:'error',message:'网络连接失败，请稍后重试。'});
    this.ws.onclose=()=>{this.dispose();if(!this.ended)this.onEvent({type:'disconnected'});};
  }
  play(data){
    if(this.closed)return;
    const samples=new Int16Array(data),buffer=this.context.createBuffer(1,samples.length,24000),values=buffer.getChannelData(0);
    for(let i=0;i<samples.length;i++)values[i]=samples[i]/32768;
    const source=this.context.createBufferSource();source.buffer=buffer;source.connect(this.context.destination);
    this.next=Math.max(this.next,this.context.currentTime+.035);
    source.start(this.next);this.next+=buffer.duration;this.sources.add(source);
    source.onended=()=>this.sources.delete(source);
  }
  interrupt(){for(const source of this.sources){try{source.stop();}catch{}}this.sources.clear();this.next=0;}
  stopAnswer(){this.suppressed=true;this.interrupt();}
  stop(){
    this.stopping=true;this.ready=false;this.stream?.getTracks().forEach(t=>t.stop());this.interrupt();
    if(this.ws?.readyState===WebSocket.OPEN){this.ws.send(JSON.stringify({type:'stop'}));this.stopTimer=setTimeout(()=>this.dispose(),8000);}
    else this.dispose();
  }
  dispose(){
    if(this.closed)return;this.closed=true;this.ready=false;clearTimeout(this.timer);clearTimeout(this.stopTimer);
    this.stream?.getTracks().forEach(t=>t.stop());this.interrupt();this.input?.disconnect();this.worklet?.disconnect();this.mute?.disconnect();
    this.context?.close().catch(()=>{});this.ws?.close();
  }
}
