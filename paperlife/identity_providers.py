"""真实供应商适配；缺少资质或配置时关闭，不提供假登录。"""
import os,json,time,hashlib,hmac,logging
logging.getLogger("httpx").setLevel(logging.WARNING)
import httpx
from fastapi import HTTPException

async def exchange_wechat(code):
 appid=os.getenv('WECHAT_APP_ID');secret=os.getenv('WECHAT_APP_SECRET')
 if not appid or not secret:raise HTTPException(503,'微信登录尚未配置')
 try:
  async with httpx.AsyncClient(timeout=10) as client:
   r=await client.get('https://api.weixin.qq.com/sns/jscode2session',params={'appid':appid,'secret':secret,'js_code':code,'grant_type':'authorization_code'})
   if r.status_code!=200:raise HTTPException(502,'微信登录暂不可用')
   data=r.json()
 except (httpx.HTTPError,ValueError):
  raise HTTPException(502,'微信登录暂不可用') from None
 if data.get('errcode') or not isinstance(data.get('openid'),str) or not data['openid']:raise HTTPException(401,'微信登录凭证无效')
 return appid,data['openid']

def sms_ready():return all(os.getenv(k) for k in ['TENCENTCLOUD_SECRET_ID','TENCENTCLOUD_SECRET_KEY','SMS_SDK_APP_ID','SMS_SIGN_NAME','SMS_TEMPLATE_ID'])

def sign_tc3(body,secret_id,secret_key,timestamp):
 date=time.strftime('%Y-%m-%d',time.gmtime(timestamp));host='sms.tencentcloudapi.com'
 hashed=hashlib.sha256(body.encode()).hexdigest()
 canonical='POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:'+host+'\n\ncontent-type;host\n'+hashed
 scope=date+'/sms/tc3_request'
 message='TC3-HMAC-SHA256\n'+str(timestamp)+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
 def digest(key,value):return hmac.new(key,value.encode(),hashlib.sha256).digest()
 key=digest(digest(digest(('TC3'+secret_key).encode(),date),'sms'),'tc3_request')
 signature=hmac.new(key,message.encode(),hashlib.sha256).hexdigest()
 return 'TC3-HMAC-SHA256 Credential='+secret_id+'/'+scope+', SignedHeaders=content-type;host, Signature='+signature

async def send_sms(phone,code):
 if not sms_ready():raise RuntimeError('sms_not_configured')
 body=json.dumps({'PhoneNumberSet':[phone],'SmsSdkAppId':os.environ['SMS_SDK_APP_ID'],'SignName':os.environ['SMS_SIGN_NAME'],'TemplateId':os.environ['SMS_TEMPLATE_ID'],'TemplateParamSet':[code,'5']},ensure_ascii=False,separators=(',',':'))
 now=int(time.time());headers={'Content-Type':'application/json; charset=utf-8','X-TC-Action':'SendSms','X-TC-Version':'2021-01-11','X-TC-Region':os.getenv('SMS_REGION','ap-guangzhou'),'X-TC-Timestamp':str(now),'Authorization':sign_tc3(body,os.environ['TENCENTCLOUD_SECRET_ID'],os.environ['TENCENTCLOUD_SECRET_KEY'],now)}
 async with httpx.AsyncClient(timeout=10) as client:
  r=await client.post('https://sms.tencentcloudapi.com',content=body.encode(),headers=headers)
  r.raise_for_status();data=r.json().get('Response',{});results=data.get('SendStatusSet',[])
  if len(results)!=1 or results[0].get('Code')!='Ok':raise RuntimeError('sms_rejected')
