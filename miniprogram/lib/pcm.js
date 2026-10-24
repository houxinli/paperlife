// Wire contract: signed 16-bit little-endian PCM, mono. No compressed fallback.
function join(a, b) { const out=new Uint8Array(a.length+b.length);out.set(a);out.set(b,a.length);return out; }
function tag(a, at, length) { return String.fromCharCode.apply(null,a.subarray(at,at+length)); }
class PCMStream {
  constructor(rate=16000) { this.rate=rate;this.pending=new Uint8Array(0);this.mode='detect';this.remaining=null; }
  push(buffer) {
    if (!(buffer instanceof ArrayBuffer)) throw Error('录音数据格式无效');
    this.pending=join(this.pending,new Uint8Array(buffer));
    if(this.mode==='detect') {
      if(this.pending.length<4)return new ArrayBuffer(0);
      const a=this.pending, magic=tag(a,0,4);
      if(magic==='RIFF')this.mode='wav';
      else {
        if(magic.slice(0,3)==='ID3'||magic==='OggS'||magic==='fLaC'||(a[0]===255&&(a[1]&224)===224))throw Error('设备返回了压缩音频格式，不能当作 PCM 发送');
        this.mode='pcm';
      }
    }
    if(this.mode==='wav') {
      const a=this.pending;
      if(a.length>65536)throw Error('录音 WAV 头格式无效');
      if(a.length<12)return new ArrayBuffer(0);
      if(tag(a,8,4)!=='WAVE')throw Error('录音 WAV 格式无效');
      const view=new DataView(a.buffer,a.byteOffset,a.byteLength);let at=12, format=false;
      while(at+8<=a.length) {
        const id=tag(a,at,4), size=view.getUint32(at+4,true);
        if(id==='data') {
          if(!format)throw Error('需要 16000 Hz、单声道、16 位 PCM WAV');
          this.mode='pcm';this.remaining=size;this.pending=a.slice(at+8);break;
        }
        if(size>65536)throw Error('录音 WAV 头格式无效');
        if(at+8+size>a.length)return new ArrayBuffer(0);
        if(id==='fmt ') {
          if(size<16||view.getUint16(at+8,true)!==1||view.getUint16(at+10,true)!==1||view.getUint32(at+12,true)!==this.rate||view.getUint16(at+22,true)!==16||view.getUint16(at+20,true)!==2)throw Error('需要 16000 Hz、单声道、16 位 PCM WAV');
          format=true;
        }
        at+=8+size+(size%2);
      }
      if(this.mode==='wav')return new ArrayBuffer(0);
    }
    let size=this.pending.length;
    if(this.remaining!==null)size=Math.min(size,this.remaining);
    size-=size%2;
    const out=this.pending.slice(0,size).buffer;this.pending=this.pending.slice(size);
    if(this.remaining!==null){this.remaining-=size;if(!this.remaining)this.pending=new Uint8Array(0);}
    return out;
  }
  finish() {
    if(this.pending.length||this.mode==='wav'||(this.remaining!==null&&this.remaining!==0))throw Error('录音样本不完整，请重新开始');
  }
}
function pcmToFloat(buffer) {
  if(!(buffer instanceof ArrayBuffer)||buffer.byteLength%2)throw Error('播放音频样本不完整');
  const view=new DataView(buffer), out=new Float32Array(buffer.byteLength/2);
  for(let i=0;i<out.length;i++)out[i]=view.getInt16(i*2,true)/32768;
  return out;
}
module.exports={PCMStream,pcmToFloat};
