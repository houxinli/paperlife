/* 将浏览器采样率转成 16kHz 单声道 PCM，每 20ms 发一包。 */
class Capture extends AudioWorkletProcessor {
  constructor(){super();this.sum=0;this.count=0;this.phase=0;this.frame=new Int16Array(320);this.index=0;}
  process(inputs){
    const data=inputs[0]?.[0]; if(!data)return true;
    for(const value of data){
      this.sum+=value;this.count++;this.phase+=16000;
      if(this.phase>=sampleRate){
        const v=Math.max(-1,Math.min(1,this.sum/this.count));
        this.frame[this.index++]=Math.round(v*(v<0?32768:32767));
        this.phase-=sampleRate;this.sum=0;this.count=0;
        if(this.index===320){this.port.postMessage(this.frame.buffer,[this.frame.buffer]);this.frame=new Int16Array(320);this.index=0;}
      }
    }
    return true;
  }
}
registerProcessor('life-capture',Capture);
