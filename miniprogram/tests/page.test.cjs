const test=require('node:test');const assert=require('node:assert/strict');
function pageFixture(){
  let definition;const app={api:{},entryVersion:0};
  global.Page=value=>definition=value;global.getApp=()=>app;global.wx={getFileSystemManager:()=>({unlink(){}})};
  delete require.cache[require.resolve('../pages/index/index')];require('../pages/index/index');
  const page={...definition,data:{...definition.data},app,api:app.api,epoch:0,visible:true,setData(next){Object.assign(this.data,next);}};
  return {page,app};
}
test('switching to book or folding history does not stop the ongoing microphone',()=>{
  const {page,app}=pageFixture();let stopped=0;app.call={stop(){stopped++;}};page.data.active=true;
  page.setTab({currentTarget:{dataset:{tab:'book'}}});page.toggleTranscript();
  assert.equal(page.data.tab,'book');assert.equal(page.data.expanded,false);assert.equal(stopped,0);
});
test('book response for previous profile cannot overwrite newly selected profile',async()=>{
  const {page}=pageFixture();page.data.profile={id:'fictional-a'};let resolve;
  page.api={base:()=>'/life/profiles/fictional-a',request:()=>new Promise(r=>resolve=r)};
  const pending=page.loadBook();page.clearView();page.data.profile={id:'fictional-b'};
  resolve({items:[{status:'confirmed',title:'虚构甲的故事'}],pending_count:0});await pending;assert.deepEqual(page.data.book,[]);
});
test('rejecting recording consent never prepares or starts the microphone',async()=>{
  const {page,app}=pageFixture();page.data.canTalk=true;page.consent=async()=>false;
  await page.run(async()=>{});page.start();await new Promise(resolve=>setImmediate(resolve));assert.equal(app.call,undefined);assert.equal(page.data.active,false);
});
test('incoming invite is held until active conversation stops, not applied to an old displayed profile',()=>{
  let definition;global.App=value=>definition=value;delete require.cache[require.resolve('../app')];require('../app');
  const accepted=[],app={...definition,entryVersion:0,api:{acceptInvite(q){accepted.push(q.invite);return true;}},call:{stop(){}}};
  app.captureEntry({query:{invite:'fictional_second_invite'}});
  assert.deepEqual(accepted,[]);assert.equal(app.pendingInvite.invite,'fictional_second_invite');
});
test('account selection reads display names from the authorized profile detail',async()=>{
  const {page}=pageFixture();page.loadHistory=async()=>{};page.loadBook=async()=>{};
  page.api.request=async path=>{assert.equal(path,'/life/profiles/fictional-person');return {id:'fictional-person',name:'虚构讲述者',role:'owner',narrator_name:'虚构小名',interviewer_name:'小光'};};
  await page.select({id:'fictional-person',name:'虚构讲述者',role:'owner'},true);
  assert.equal(page.data.narratorName,'虚构小名');assert.equal(page.data.interviewerName,'小光');
});
test('spoken name preference refreshes current names without restarting or switching scope',async()=>{
  const {page,app}=pageFixture();page.data.profile={id:'fictional-a',name:'虚构讲述者'};page.data.scope='guest';page.data.active=true;
  let stopped=0;app.call={stop(){stopped++;}};
  page.api.request=async(path,method,data,scope)=>{assert.equal(path,'/life/guest');assert.equal(scope,'guest');return {id:'fictional-a',name:'虚构讲述者',narrator_name:'林奶奶',interviewer_name:'小禾'};};
  page.onVoiceEvent({type:'preference_saved'});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(page.data.interviewerName,'小禾');assert.equal(page.data.narratorName,'林奶奶');assert.equal(stopped,0);assert.equal(page.data.active,true);
});
