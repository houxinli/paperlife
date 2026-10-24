const {invoke}=require('../../lib/api');
const {VoiceCall}=require('../../lib/voice');
const {updateTranscript}=require('../../lib/protocol');

Page({
  data:{configured:false,error:'',busy:false,scope:'account',loggedIn:false,hasInvite:false,profiles:[],profile:null,profileName:'',canTalk:false,tab:'chat',active:false,callState:'准备好时，我们慢慢聊',audioBlocked:false,expanded:true,rows:[],currentText:'',history:[],hasMore:false,historyBusy:false,detail:null,book:[],pendingCount:0,privacyPrompt:false,playing:false},
  onLoad(){
    this.app=getApp();this.api=this.app.api;this.epoch=0;this.visible=true;
    wx.hideShareMenu({menus:['shareAppMessage','shareTimeline']});
    this.privacyListener=resolve=>{if(this.privacyResolve)this.privacyResolve({event:'disagree'});this.privacyResolve=resolve;this.setData({privacyPrompt:true});};
    if(wx.onNeedPrivacyAuthorization)wx.onNeedPrivacyAuthorization(this.privacyListener);
    if(!this.api){this.setData({error:this.app.configurationError||'小程序尚未配置，请联系开发者'});return;}
    this.setData({configured:true});this.seenEntry=-1;
  },
  onShow(){
    this.visible=true;if(!this.api)return;
    this.applyPendingInvite();
    if(this.seenEntry!==this.app.entryVersion){
      this.seenEntry=this.app.entryVersion;
      if(this.app.call){this.app.call.stop();this.setData({error:'已收到新的专属邀请，请在当前聊天保存后再打开'});return;}
      this.setData({scope:this.api.invite?'guest':'account'});this.run(()=>this.refresh());
    }else if(!this.data.profile&&!this.data.busy)this.run(()=>this.refresh());
  },
  onHide(){this.visible=false;this.stopPlayback();if(this.app.call)this.app.call.stop();},
  onUnload(){
    this.visible=false;this.epoch++;this.stopPlayback();if(this.app.call){this.app.call.dispose();this.app.call=null;}
    if(this.privacyResolve)this.privacyResolve({event:'disagree'});
    if(wx.offNeedPrivacyAuthorization)wx.offNeedPrivacyAuthorization(this.privacyListener);
  },
  async run(work){
    if(this.data.busy)return;this.setData({busy:true,error:''});
    try{await work();}catch(error){this.setData({error:error.message||'操作未完成，请稍后重试',loggedIn:!!(this.api&&this.api.session),hasInvite:!!(this.api&&this.api.invite)});}
    finally{this.setData({busy:false});}
  },
  clearView(){this.epoch++;this.stopPlayback();this.setData({profile:null,canTalk:false,history:[],hasMore:false,detail:null,book:[],pendingCount:0,rows:[],currentText:''});},
  applyPendingInvite(){
    if(this.app.call||!this.app.pendingInvite)return false;
    const query=this.app.pendingInvite;this.app.pendingInvite=null;this.app.captureEntry({query});
    this.setData({scope:this.api.invite?'guest':'account'});return true;
  },
  async refresh(){
    this.clearView();this.setData({loggedIn:!!this.api.session,hasInvite:!!this.api.invite,profiles:[]});
    if(this.app.entryError)throw Error(this.app.entryError);
    if(this.data.scope==='guest'){
      const profile=await this.api.request('/life/guest','GET',null,'guest');await this.select(profile,profile.configured);return;
    }
    if(!this.api.session)return;
    const [profiles,provider]=await Promise.all([this.api.request('/life/profiles'),this.api.request('/life/provider')]);
    this.providerConfigured=!!provider.configured;this.setData({profiles});
    if(profiles.length)await this.select(profiles[0],this.providerConfigured);
  },
  login(){this.run(async()=>{if(this.data.active)return;await this.api.login();this.app.entryError='';this.setData({scope:'account'});await this.refresh();});},
  logout(){this.run(async()=>{if(this.data.active)return;try{await this.api.logout();}finally{this.clearView();this.setData({loggedIn:false,profiles:[]});}});},
  useInvite(){this.run(async()=>{if(this.data.active)return;this.app.entryError='';this.setData({scope:'guest'});await this.refresh();});},
  useAccount(){this.run(async()=>{if(this.data.active)return;this.app.entryError='';this.setData({scope:'account'});await this.refresh();});},
  forgetInvite(){this.run(async()=>{if(this.data.active)return;this.api.save('invite','');this.app.entryError='';this.setData({scope:'account',hasInvite:false});await this.refresh();});},
  onNameInput(e){this.setData({profileName:e.detail.value});},
  createProfile(){this.run(async()=>{
    if(this.data.active||this.data.scope!=='account')return;
    const name=this.data.profileName.trim()||'我的人生故事';
    await this.api.request('/life/profiles','POST',{name,context:''});this.setData({profileName:''});await this.refresh();
  });},
  chooseProfile(e){this.run(async()=>{if(this.data.active)return;const profile=this.data.profiles.find(p=>p.id===e.currentTarget.dataset.id);if(profile)await this.select(profile,this.providerConfigured);});},
  async select(profile,configured){
    this.clearView();const epoch=this.epoch;
    if(this.data.scope==='account')profile=await this.api.request('/life/profiles/'+encodeURIComponent(profile.id));
    if(epoch!==this.epoch)return;
    this.setData({profile,canTalk:!!configured&&profile.role!=='reader',narratorName:profile.narrator_name||profile.name,interviewerName:profile.interviewer_name||'拾光'});
    await Promise.all([this.loadHistory(true),this.loadBook()]);
  },
  setTab(e){this.setData({tab:e.currentTarget.dataset.tab});},
  toggleTranscript(){this.setData({expanded:!this.data.expanded});},
  async loadHistory(reset=false){
    if(!this.data.profile)return;
    const epoch=this.epoch,requestId=(this.historyRequest||0)+1,offset=reset?0:this.data.history.length,scope=this.data.scope;this.historyRequest=requestId;
    const result=await this.api.request(this.api.base(this.data.profile,scope)+'/history?offset='+offset+'&limit=20','GET',null,scope);
    if(epoch!==this.epoch||requestId!==this.historyRequest)return;
    this.setData({history:reset?result.sessions:this.data.history.concat(result.sessions),hasMore:!!result.has_more});
  },
  moreHistory(){if(this.data.historyBusy)return;this.setData({historyBusy:true});this.loadHistory().catch(e=>this.setData({error:e.message})).finally(()=>this.setData({historyBusy:false}));},
  async loadBook(){
    if(!this.data.profile)return;const epoch=this.epoch,scope=this.data.scope;
    const result=await this.api.request(this.api.base(this.data.profile,scope)+'/book','GET',null,scope);
    if(epoch===this.epoch)this.setData({book:(result.items||[]).filter(i=>i.status==='confirmed'),pendingCount:result.pending_count||0});
  },
  refreshReading(){this.run(()=>Promise.all([this.loadHistory(true),this.loadBook()]));},
  openHistory(e){this.openSession(e.currentTarget.dataset.id);},
  openSession(id){this.run(async()=>{const epoch=this.epoch;this.stopPlayback();const detail=await this.api.request(this.api.detail(id,this.data.scope),'GET',null,this.data.scope);if(epoch===this.epoch)this.setData({detail,tab:'history'});});},
  closeHistory(){this.stopPlayback();this.setData({detail:null});},
  async consent(){
    if(!wx.requirePrivacyAuthorize)throw Error('请更新微信，以查看和确认隐私保护指引');
    const decision=await invoke(wx,'showModal',{title:'开始这次语音聊天？',content:'拾光是 AI 采访者。您的声音会实时发送到服务端用于语音交流，并保存原声和原始转写。经您或获授权家人确认的整理内容才收入人生之书。可以随时说结束或点击结束按钮。',confirmText:'同意并开始',cancelText:'暂不开始'});
    if(!decision.confirm)return false;
    await invoke(wx,'requirePrivacyAuthorize');
    await invoke(wx,'authorize',{scope:'scope.record'});return true;
  },
  start(){this.run(async()=>{
    if(this.app.call||!this.data.canTalk)return;
    if(!await this.consent()||!this.visible)return;
    this.stopPlayback();const call=new VoiceCall(wx,e=>{if(this.app.call===call)this.onVoiceEvent(e);});this.app.call=call;
    this.setData({active:true,rows:[],currentText:'',callState:'正在连接…',audioBlocked:false});
    try{
      await call.prepare();if(call.closed||call.stopping||!this.visible)return;
      const session=await this.api.startSession(this.data.profile,this.data.scope);
      if(call.closed||call.stopping||!this.visible)return;
      call.connect(this.api.socketUrl,session.ticket);
    }catch(error){call.dispose();if(this.app.call===call)this.app.call=null;this.setData({active:false,callState:'这次没有接通'});throw error;}
    finally{if(call.closed&&this.app.call===call){this.app.call=null;this.setData({active:false});}}
  });},
  stop(){if(this.app.call)this.app.call.stop();},
  resumeAudio(){this.run(async()=>{if(this.app.call)await this.app.call.resumeAudio();});},
  onVoiceEvent(event){
    if(event.type==='recording')this.setData({callState:'正在听您说 · 麦克风已打开'});
    if(event.type==='audio_start')this.setData({callState:this.data.interviewerName+'在说话 · 您也可以开口'});
    if(event.type==='interrupt')this.setData({callState:'正在听您说'});
    if(event.type==='transcript'){
      const rows=updateTranscript(this.data.rows,event);this.setData({rows,currentText:event.text||this.data.currentText});
      // No automatic scroll: reading older conversation must not lose its position.
    }
    if(event.type==='audio_state')this.setData({audioBlocked:event.state==='blocked'});
    if(event.type==='notice'||event.type==='error')this.setData({error:event.message||'语音服务暂时不可用'});
    if(event.type==='ending')this.setData({callState:'麦克风已关闭，正在保存…'});
    if(event.type==='ended'||event.type==='disconnected'){
      this.app.call=null;this.setData({active:false,audioBlocked:false,callState:event.type==='ended'&&event.status==='ended'?'这次聊天已保存，下次接着聊':'连接已结束；已收到的内容会保留'});
      if(this.applyPendingInvite()){this.seenEntry=this.app.entryVersion;this.refresh().catch(e=>this.setData({error:e.message}));return;}
      Promise.all([this.loadHistory(true),this.loadBook()]).catch(e=>this.setData({error:e.message}));
    }
  },
  privacyAgree(){const resolve=this.privacyResolve;this.privacyResolve=null;this.setData({privacyPrompt:false});if(resolve)resolve({event:'agree',buttonId:'privacy-agree'});},
  privacyDecline(){const resolve=this.privacyResolve;this.privacyResolve=null;this.setData({privacyPrompt:false});if(resolve)resolve({event:'disagree'});},
  openPrivacy(){if(wx.openPrivacyContract)wx.openPrivacyContract({fail:()=>this.setData({error:'隐私保护指引尚未配置，请联系开发者'})});},
  openSettings(){wx.openSetting({});},
  playHistory(e){this.run(async()=>{
    if(this.data.active||!this.data.detail)return;
    this.stopPlayback();const epoch=this.epoch,detailId=this.data.detail.id,track=e.currentTarget.dataset.track==='assistant'?'assistant':'user';
    const path=this.api.detail(detailId,this.data.scope)+'/audio?track='+track;
    const result=await invoke(wx,'downloadFile',{url:this.api.apiBaseUrl+path,header:this.api.headers(this.data.scope,path),timeout:30000});
    if(result.statusCode!==200)throw Error('这段录音暂时无法回听');
    if(epoch!==this.epoch||!this.visible||!this.data.detail||this.data.detail.id!==detailId){this.deleteTemp(result.tempFilePath);return;}
    this.audioFile=result.tempFilePath;this.audio=wx.createInnerAudioContext();this.audio.src=this.audioFile;
    this.audio.onEnded(()=>this.stopPlayback());this.audio.onError(()=>{this.stopPlayback();this.setData({error:'这台设备暂时不能播放该录音'});});
    this.audio.play();this.setData({playing:true});
  });},
  deleteTemp(path){if(path)wx.getFileSystemManager().unlink({filePath:path,fail:()=>{}});},
  stopPlayback(){if(this.audio){this.audio.stop();this.audio.destroy();this.audio=null;}this.deleteTemp(this.audioFile);this.audioFile='';this.setData({playing:false});}
});
