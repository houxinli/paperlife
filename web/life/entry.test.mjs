import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveNarratorEntry} from './entry.js';
const storage=()=>{const data=new Map();return {getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)};};
test('original link is reusable and survives a new browser tab',()=>{
 const local=storage();const token='a'.repeat(43);
 const first=resolveNarratorEntry('#invite='+token,storage(),local);
 assert.equal(first.token,token);assert.equal(first.hash,'#invite='+token);
 const reopened=resolveNarratorEntry('',storage(),local);
 assert.equal(reopened.token,token);assert.equal(reopened.hash,first.hash);
});
test('explicit new link wins and legacy session entry migrates',()=>{
 const local=storage(),session=storage();session.setItem('paperlife_narrator_token','b'.repeat(43));
 assert.equal(resolveNarratorEntry('',session,local).token,'b'.repeat(43));
 assert.equal(resolveNarratorEntry('#invite='+'c'.repeat(43),session,local).token,'c'.repeat(43));
});
