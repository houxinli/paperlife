const {AudioGate}=require('./audio-gate');
class OrderedSender {
  constructor(socket,onError,options={}){this.socket=socket;this.onError=onError;this.maxBytes=options.maxBytes||128000;this.timeout=options.timeout||5000;this.queue=[];this.bytes=0;this.closed=false;this.active=null;}
  send(data){
    if(this.closed)return false;
    const size=typeof data==='string'?data.length*2:data.byteLength;
    if(this.bytes+size>this.maxBytes){this.fail(Error('网络上传跟不上，已停止录音，请换稳定网络后继续'));return false;}
    this.queue.push({data,size});this.bytes+=size;this.pump();return true;
  }
  pump(){
    if(this.closed||this.active||!this.queue.length)return;
    const entry=this.queue.shift();this.active=entry;
    this.timer=setTimeout(()=>this.fail(Error('语音上传超时，已停止录音')),this.timeout);
    try{this.socket.send({data:entry.data,success:()=>{
      if(this.closed||this.active!==entry)return;clearTimeout(this.timer);this.bytes-=entry.size;this.active=null;this.pump();
    },fail:()=>this.fail(Error('语音上传失败，已停止录音'))});}catch{this.fail(Error('语音连接已断开'));}
  }
  dropAudio(){this.queue=this.queue.filter(entry=>{if(typeof entry.data==='string')return true;this.bytes-=entry.size;return false;});}
  fail(error){if(this.closed)return;this.close();this.onError(error);}
  close(){this.closed=true;clearTimeout(this.timer);this.queue=[];this.active=null;this.bytes=0;}
}
function updateTranscript(rows,event){
  // 打断只冻结已经显示的助手原话；下一回答另起一行，不改动输入快照。
  if(event.type==='interrupt')return rows.map(row=>row.role==='assistant'&&!row.final?{...row,final:true,interrupted:true}:row);
  if(!['user','assistant'].includes(event.role)||typeof event.text!=='string')return rows;
  const next=rows.slice();let at=-1;
  for(let i=next.length-1;i>=0;i--)if(next[i].role===event.role&&!next[i].final){at=i;break;}
  const row={id:at<0?'turn-'+next.length:next[at].id,role:event.role,text:event.text,final:!!event.final};
  if(at<0)next.push(row);else next[at]=row;return next;
}
module.exports={AudioGate,OrderedSender,updateTranscript};
