// 供后端测试回放实际代理输出；仅使用合成 PCM 的字节数，不读取录音或用户资料。
import {AudioGate as WebGate} from '../web/life/audio-gate.js';
import native from '../miniprogram/lib/audio-gate.js';
const chunks=[];
for await(const chunk of process.stdin)chunks.push(chunk);
const trace=JSON.parse(Buffer.concat(chunks).toString('utf8'));
const results=[];
for(const [Gate,options] of [[WebGate,{allowLegacyAudio:true}],[native.AudioGate,{}]]) {
  const gate=new Gate(options);let played=0;
  for(const message of trace) {
    if(message.event)gate.event(message.event);
    else if(gate.accepts())played+=message.pcm_bytes;
  }
  results.push({played_bytes:played,open:gate.accepts()});
}
process.stdout.write(JSON.stringify(results));
