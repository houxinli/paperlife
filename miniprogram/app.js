const {Api}=require('./lib/api');
const config=require('./config');
App({
  onLaunch(options){
    this.entryVersion=0;
    try{this.api=new Api(wx,config,wx.getAccountInfoSync().miniProgram.appId);this.captureEntry(options);}
    catch(error){this.configurationError=error.message;}
  },
  onShow(options){if(this.api)this.captureEntry(options);},
  captureEntry(options){
    const query=(options&&options.query)||{};
    if(this.call&&Object.prototype.hasOwnProperty.call(query,'invite')){this.pendingInvite=query;this.call.stop();return;}
    try{if(this.api.acceptInvite(query)){this.entryVersion++;this.entryError='';}}
    catch(error){this.entryVersion++;this.entryError=error.message;}
  },
  onHide(){if(this.call)this.call.stop();}
});
