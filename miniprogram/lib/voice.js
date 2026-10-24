const {PCMStream,pcmToFloat}=require('./pcm');
const {AudioGate,OrderedSender}=require('./protocol');
let microphoneOwner=null;

class VoiceCall {
  constructor(wx,onEvent){this.wx=wx;this.onEvent=onEvent;this.gate=new AudioGate();this.sources=new Set();this.next=0;this.closed=false;this.stopping=false;this.ready=false;this.startedMic=false;this.bindings=[];}
  async prepare(){
    const info=this.wx.getDeviceInfo?this.wx.getDeviceInfo():this.wx.getSystemInfoSync();
    if(!['ios','android','ohos'].includes(info.platform))throw Error('请在手机微信真机中使用语音；桌面与模拟器无法验证 16k 录音');
    if(!this.wx.getRecorderManager||!this.wx.createWebAudioContext)throw Error('当前微信版本不支持实时语音，请更新微信');
    if(microphoneOwner)throw Error('上一段录音正在停止，请稍后再试');
    this.recorder=this.wx.getRecorderManager();
    if(!this.recorder.onFrameRecorded||!this.recorder.offFrameRecorded)throw Error('当前微信不支持 PCM 分帧录音，请更新微信');
    this.context=this.wx.createWebAudioContext();
    if(!this.context.createBuffer||!this.context.createBufferSource)throw Error('当前微信不支持原始语音播放');
    this.context.onstatechange=()=>{
      if(!this.closed&&!this.stopping&&this.context.state!=='running')this.onEvent({type:'audio_state',state:'blocked',message:'声音暂停了，请点恢复声音'});
    };
    await this.resumeAudio();
  }
  async resumeAudio(){
    if(this.closed||this.stopping||!this.context)return;
    let timer;
    try{
      await Promise.race([Promise.resolve(this.context.resume()),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('声音未恢复，请再次点击恢复声音')),3000);})]);
      if(!this.closed&&!this.stopping){
        if(this.context.state!=='running')throw Error('声音未恢复，请再次点击恢复声音');
        this.onEvent({type:'audio_state',state:'ready'});
      }
    }finally{clearTimeout(timer);}
  }
  connect(url,ticket){
    if(this.closed||this.stopping)return;
    if(typeof ticket!=='string'||!ticket)throw Error('服务器没有返回语音票据');
    this.socket=this.wx.connectSocket({url,timeout:20000,fail:()=>this.fail('语音网络连接失败，请稍后重试')});
    this.sender=new OrderedSender(this.socket,e=>this.fail(e.message));
    this.timer=setTimeout(()=>this.fail('语音连接超时，请检查服务配置'),40000);
    this.socket.onOpen(()=>{if(this.closed)return;this.open=true;this.sender.send(JSON.stringify({ticket}));});
    this.socket.onMessage(({data})=>{
      if(this.closed)return;
      try{
        if(data instanceof ArrayBuffer){if(this.gate.accepts()&&!this.stopping)this.play(data);return;}
        const event=JSON.parse(data);if(!event||typeof event.type!=='string')throw Error('语音消息格式无效');
        if(event.type==='ready'&&!this.ready&&!this.stopping){clearTimeout(this.timer);this.ready=true;this.startMicrophone();}
        this.gate.event(event);
        if(event.type==='interrupt')this.interrupt();
        if(event.type==='ending')this.beginEnding();
        if(event.type==='ended'){this.ended=true;this.dispose();}
        this.onEvent(event);
        if(event.type==='error')this.stop();
      }catch(error){this.fail(error.message||'语音消息无法处理');}
    });
    this.socket.onError(()=>this.fail('语音连接失败，已停止录音'));
    this.socket.onClose(()=>{const expected=this.ended;this.dispose();if(!expected&&!this.closeReported){this.closeReported=true;this.onEvent({type:'disconnected'});}});
  }
  bind(name,fn){this.recorder['on'+name](fn);this.bindings.push([name,fn]);}
  releaseMicrophone(){
    clearTimeout(this.frameTimer);
    for(const [name,fn] of this.bindings){const off=this.recorder['off'+name];if(off)off.call(this.recorder,fn);}
    this.bindings=[];if(microphoneOwner===this)microphoneOwner=null;this.startedMic=false;
  }
  startMicrophone(){
    if(this.closed||this.stopping||this.startedMic)return;
    if(microphoneOwner&&microphoneOwner!==this)throw Error('录音设备仍被占用');
    microphoneOwner=this;this.startedMic=true;this.pcm=new PCMStream(16000);
    const watch=()=>{clearTimeout(this.frameTimer);this.frameTimer=setTimeout(()=>this.fail('没有收到实时 PCM 录音分片，请更新微信或更换手机再试'),5000);};
    this.bind('Start',()=>{if(this.closed||this.stopping){this.recorder.stop();return;}watch();this.onEvent({type:'recording'});});
    this.bind('FrameRecorded',event=>{
      if(this.closed||this.stopping||!this.ready)return;
      try{watch();const data=this.pcm.push(event.frameBuffer);if(data.byteLength)this.sender.send(data);if(event.isLastFrame)this.pcm.finish();}catch(error){this.fail(error.message);}
    });
    this.bind('Stop',result=>{
      const unexpected=!this.closed&&!this.stopping;this.releaseMicrophone();
      if(result&&result.tempFilePath&&this.wx.getFileSystemManager)this.wx.getFileSystemManager().unlink({filePath:result.tempFilePath,fail:()=>{}});
      if(unexpected){this.onEvent({type:'notice',message:'本次录音已到时或被系统停止，保存后可继续聊'});this.stop();}
    });
    this.bind('Error',()=>{this.releaseMicrophone();this.fail('录音失败，请检查麦克风权限和手机设置');});
    this.bind('InterruptionBegin',()=>{this.onEvent({type:'notice',message:'录音被电话或系统打断，本次聊天正在保存'});this.stop();});
    this.bind('Pause',()=>{if(!this.stopping&&!this.closed)this.stop();});
    watch();
    // Official option value is upper-case PCM. MP3/AAC must never use this wire path.
    try{this.recorder.start({duration:600000,sampleRate:16000,numberOfChannels:1,format:'PCM',frameSize:2,audioSource:'auto'});}catch(error){this.releaseMicrophone();throw error;}
  }
  stopMicrophone(){clearTimeout(this.frameTimer);if(this.startedMic){try{this.recorder.stop();}catch{/* onStart will stop a recorder that was still starting. */}}}
  play(data){
    const values=pcmToFloat(data);if(!values.length)return;
    if(this.next-this.context.currentTime>30)throw Error('播放队列过长，已停止本次聊天，请检查声音设置');
    const buffer=this.context.createBuffer(1,values.length,24000);buffer.getChannelData(0).set(values);
    const source=this.context.createBufferSource();source.buffer=buffer;source.connect(this.context.destination);
    this.next=Math.max(this.next,this.context.currentTime+.035);source.onended=()=>{this.sources.delete(source);try{source.disconnect();}catch{}if(!this.sources.size&&!this.closed&&!this.stopping&&this.ready&&this.context.state==='running')this.onEvent({type:'audio_state',state:'ready'});};
    this.sources.add(source);source.start(this.next);this.next+=buffer.duration;
  }
  interrupt(){for(const source of this.sources){try{source.stop();source.disconnect();}catch{}}this.sources.clear();this.next=0;}
  beginEnding(){
    if(this.stopping)return;this.stopping=true;this.ready=false;clearTimeout(this.timer);this.stopMicrophone();this.interrupt();
    if(this.sender)this.sender.dropAudio();
    this.stopTimer=setTimeout(()=>{this.dispose();if(!this.ended)this.onEvent({type:'disconnected'});},12000);
  }
  stop(){
    if(this.closed||this.stopping)return;
    this.gate.event({type:'ending'});this.beginEnding();this.onEvent({type:'ending'});
    if(this.open&&this.sender&&!this.sender.closed)this.sender.send(JSON.stringify({type:'stop'}));
    else{this.dispose();if(!this.closeReported){this.closeReported=true;this.onEvent({type:'disconnected'});}}
  }
  fail(message){if(this.closed)return;this.onEvent({type:'error',message});this.dispose();if(!this.closeReported){this.closeReported=true;this.onEvent({type:'disconnected'});}}
  dispose(){
    if(this.closed)return;this.closed=true;this.ready=false;this.stopping=true;clearTimeout(this.timer);clearTimeout(this.stopTimer);clearTimeout(this.frameTimer);
    this.stopMicrophone();this.interrupt();if(this.sender)this.sender.close();
    if(this.context){this.context.onstatechange=null;try{Promise.resolve(this.context.close()).catch(()=>{});}catch{}}
    if(this.socket){try{this.socket.close({code:1000,reason:'client finished'});}catch{}}
  }
}
module.exports={VoiceCall};
