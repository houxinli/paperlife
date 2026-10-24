function validateConfig(config,appId){
  if(!/^wx[0-9a-f]{16}$/i.test(appId||'')||appId==='wx0000000000000000')throw Error('请先在微信开发者工具配置真实小程序 AppID');
  const base=String(config.apiBaseUrl||'').replace(/\/+$/,'');
  if(!/^https:\/\/[a-z0-9.-]+(?::\d+)?(?:\/[a-z0-9._~-]+)*\/api$/i.test(base))throw Error('请在 config.js 配置 HTTPS API 地址（以 /api 结尾）');
  return {apiBaseUrl:base,socketUrl:base.replace(/^https:/,'wss:')+'/life/live'};
}
function invoke(wx,name,options={}){return new Promise((resolve,reject)=>wx[name]({...options,success:resolve,fail:()=>reject(Error(name==='login'?'微信登录失败，请重试':'操作未完成，请检查授权或网络'))}));}
class Api {
  constructor(wx,config,appId){
    this.wx=wx;Object.assign(this,validateConfig(config,appId));this.key='paperlife:'+appId+':'+this.apiBaseUrl+':';
    this.session=this.read('session');this.invite=this.read('invite');
  }
  read(kind){try{const value=this.wx.getStorageSync(this.key+kind);return typeof value==='string'?value:'';}catch{return '';}}
  save(kind,value){this[kind]=value;try{if(value)this.wx.setStorageSync(this.key+kind,value);else this.wx.removeStorageSync(this.key+kind);}catch{/* Keep this session in memory if storage is unavailable. */}}
  acceptInvite(query={}){
    if(!Object.prototype.hasOwnProperty.call(query,'invite'))return false;
    this.save('invite','');const value=query.invite;
    if(typeof value!=='string'||! /^[A-Za-z0-9_-]{20,200}$/.test(value))throw Error('专属邀请无效，请联系家人重新发送');
    this.save('invite',value);return true;
  }
  headers(scope,path){
    const headers={'Content-Type':'application/json'};
    if(scope==='guest'){
      if(!/^\/life\/guest(?:\/|\?|$)/.test(path))throw Error('专属邀请只能访问绑定档案');
      if(!this.invite)throw Error('请使用家人发送的专属邀请');headers['X-Life-Link']=this.invite;
    }else if(scope==='account'){
      if(!this.session)throw Error('请先微信登录');headers.Authorization='Bearer '+this.session;
    }
    return headers;
  }
  async request(path,method='GET',data=null,scope='account'){
    if(!/^\/(?:auth|life)\//.test(path)&&path!=='/life/guest')throw Error('接口路径无效');
    const header=this.headers(scope,path),response=await invoke(this.wx,'request',{url:this.apiBaseUrl+path,method,data,header,timeout:20000});
    if(response.statusCode<200||response.statusCode>=300){
      if(response.statusCode===401)this.save(scope==='guest'?'invite':'session','');
      const detail=response.data&&response.data.detail;
      const error=Error(typeof detail==='string'?detail:'服务暂时不可用，请稍后重试');error.status=response.statusCode;throw error;
    }
    return response.data;
  }
  async login(){
    const {code}=await invoke(this.wx,'login',{timeout:10000});if(!code)throw Error('微信未返回登录凭证');
    const result=await this.request('/auth/wechat','POST',{code},'none');
    if(!result||typeof result.session_id!=='string'||!result.session_id)throw Error('服务器未返回有效登录会话');
    this.save('session',result.session_id);return result.user;
  }
  async logout(){try{if(this.session)await this.request('/auth/logout','POST');}finally{this.save('session','');}}
  base(profile,scope){if(scope==='guest')return '/life/guest';if(!profile||!profile.id)throw Error('请先选择档案');return '/life/profiles/'+encodeURIComponent(profile.id);}
  detail(id,scope){return scope==='guest'?'/life/guest/history/'+encodeURIComponent(id):'/life/sessions/'+encodeURIComponent(id);}
  async startSession(profile,scope){return this.request(this.base(profile,scope)+'/sessions','POST',{transport:'wechat'},scope);}
}
module.exports={Api,validateConfig,invoke};
