#!/usr/bin/env node
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const args=process.argv.slice(2);
let root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),check=false;
for(let index=0;index<args.length;index++) {
  if(args[index]==='--check')check=true;
  else if(args[index]==='--root'&&args[index+1]&&!args[index+1].startsWith('--'))root=resolve(args[++index]);
  else {
    console.error('Usage: node scripts/sync_shared.mjs [--check] [--root directory]');
    process.exit(2);
  }
}
const source=await readFile(join(root,'shared/audio-gate.js'),'utf8');
const hash=createHash('sha256').update(source).digest('hex');
const banner=`// 自动生成：请修改 shared/audio-gate.js，再运行 node scripts/sync_shared.mjs。\n// 源文件 SHA256: ${hash}\n`;
const outputs=[['web/life/audio-gate.js','export {AudioGate};'],
  ['miniprogram/lib/audio-gate.js','module.exports={AudioGate};']];
let drift=false;
for(const [relative,wrapper] of outputs) {
  const path=join(root,relative),expected=banner+source.trimEnd()+'\n\n'+wrapper+'\n';
  const actual=await readFile(path,'utf8').catch(error=>{if(error.code==='ENOENT')return null;throw error;});
  if(actual===expected)continue;
  if(check) { console.error('Shared source drift: '+relative);drift=true; }
  else { await mkdir(dirname(path),{recursive:true});await writeFile(path,expected);console.log('Generated '+relative); }
}
if(drift)process.exitCode=1;
else if(check)console.log('Shared client outputs are current.');
