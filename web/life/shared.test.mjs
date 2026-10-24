import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';

const root=new URL('../../',import.meta.url);
const require=createRequire(import.meta.url);

test('both shipped module formats obey the same audio boundary scenarios',async()=>{
  const contract=JSON.parse(await readFile(new URL('contracts/conversation-v1.json',root),'utf8'));
  const {AudioGate:WebGate}=await import('./audio-gate.js');
  const {AudioGate:NativeGate}=require('../../miniprogram/lib/audio-gate.js');
  for(const Gate of [WebGate,NativeGate])for(const scenario of contract.gate_scenarios){
    const gate=new Gate(scenario.options);
    for(const step of scenario.steps){
      if(step.event)gate.event(step.event);
      if(step.action)gate[step.action]();
      assert.equal(gate.accepts(),step.accepts,scenario.name+' / '+JSON.stringify(step));
    }
  }
});

test('generator checks missing or edited build outputs without silently repairing them',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'paperlife-core-'));
  try{
    await mkdir(join(directory,'shared'));
    await writeFile(join(directory,'shared/audio-gate.js'),'class AudioGate { accepts() { return true; } }\n');
    const script=new URL('scripts/sync_shared.mjs',root);
    const run=(...args)=>spawnSync(process.execPath,[script.pathname,'--root',directory,...args],{encoding:'utf8'});
    assert.equal(run('--check').status,1,'missing outputs must fail');
    const generated=run();assert.equal(generated.status,0,generated.stderr);
    assert.equal(run('--check').status,0);
    // 真正加载两种产物，避免只核对文件存在或包装文字。
    await writeFile(join(directory,'package.json'),'{"type":"module"}');
    await writeFile(join(directory,'miniprogram/package.json'),'{"type":"commonjs"}');
    const {AudioGate:WebGate}=await import(pathToFileURL(join(directory,'web/life/audio-gate.js')));
    const {AudioGate:NativeGate}=require(join(directory,'miniprogram/lib/audio-gate.js'));
    assert.equal(new WebGate().accepts(),true);assert.equal(new NativeGate().accepts(),true);
    for(const relative of ['web/life/audio-gate.js','miniprogram/lib/audio-gate.js']){
      const path=join(directory,relative),edited=(await readFile(path,'utf8'))+'// drift\n';
      await writeFile(path,edited);
      assert.equal(run('--check').status,1,relative+' drift must fail');
      assert.equal(await readFile(path,'utf8'),edited,'check must not rewrite the file');
      assert.equal(run().status,0);
    }
  }finally{await rm(directory,{recursive:true,force:true});}
});
