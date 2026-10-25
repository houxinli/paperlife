import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ProcessingTests(unittest.IsolatedAsyncioTestCase):
 async def test_generation_drafts_citations_and_idempotent_commit(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import enqueue,process_one
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试人物','');s=store.create_session(p['id'],1,'求学')
   store.add_turn(s['id'],'user','我在测试镇学画画，老师叫测试人物甲。',2.5);store.finish_session(s['id'],'ended')
   turn=store.turns(s['id'])[0]
   async def generate(stage,turns):
    cite={'turn_id':turn['id'],'quote':'我在测试镇学画画'}
    if stage=='extract':return {'items':[{'kind':'event','title':'学画画','body':'在测试镇学画画','citations':[cite]}]}
    return {'stories':[{'title':'画画的回忆','paragraphs':[{'text':'我在测试镇学画画。','citations':[cite]}]}]}
   enqueue(store,s['id']);enqueue(store,s['id'])
   self.assertTrue(await process_one(store,generate));self.assertFalse(await process_one(store,generate))
   with store.db() as db:
    items=[dict(r) for r in db.execute('SELECT * FROM items')];job=dict(db.execute('SELECT * FROM processing_jobs').fetchone())
   self.assertEqual(len(items),2);self.assertEqual({i['status'] for i in items},{'draft'})
   self.assertEqual(job['status'],'completed')
   evidence=store.item(items[0]['id'])['evidence'];self.assertEqual(evidence[0]['turn_id'],turn['id']);self.assertEqual(evidence[0]['seconds'],2.5)
   self.assertEqual(store.turns(s['id'])[0]['text'],'我在测试镇学画画，老师叫测试人物甲。')

 async def test_invalid_evidence_never_creates_content(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import enqueue,process_one
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试','');s=store.create_session(p['id'],1,'test')
   store.add_turn(s['id'],'user','这一段没有提到出生年份。',0);store.finish_session(s['id'],'ended')
   async def bad(stage,turns):return {'items':[{'kind':'event','title':'出生','body':'凭空添加','citations':[{'turn_id':999,'quote':'不存在的引文'}]}]}
   enqueue(store,s['id']);await process_one(store,bad)
   with store.db() as db:
    self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0],0)
    self.assertEqual(db.execute('SELECT status FROM processing_jobs').fetchone()[0],'pending')

 async def test_empty_session_not_queued_and_restart_can_claim_expired_job(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import enqueue,claim
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试','');s=store.create_session(p['id'],1,'test');store.finish_session(s['id'],'ended')
   self.assertIsNone(enqueue(store,s['id']))
   store.add_turn(s['id'],'user','人工虚构的测试句子',0);enqueue(store,s['id']);self.assertIsNotNone(claim(store))
   with store.db() as db:db.execute("UPDATE processing_jobs SET lease_until=0")
   self.assertIsNotNone(claim(store))

 async def test_story_failure_resumes_checkpoint_without_reextracting(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import enqueue,process_one
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试','');s=store.create_session(p['id'],1,'test');store.add_turn(s['id'],'user','这是一条人工虚构的访谈记录。',0);store.finish_session(s['id'],'ended');turn=store.turns(s['id'])[0]
   calls=[]
   async def broken(stage,turns):
    calls.append(stage)
    if stage=='story':raise TimeoutError()
    return {'items':[{'kind':'fact','title':'测试','body':'测试记录','citations':[{'turn_id':turn['id'],'quote':'人工虚构的访谈记录'}]}]}
   enqueue(store,s['id']);await process_one(store,broken)
   with store.db() as db:db.execute('UPDATE processing_jobs SET next_run=0')
   async def recover(stage,turns):
    calls.append(stage);return {'stories':[]}
   await process_one(store,recover)
   self.assertEqual(calls,['extract','story','story'])
   with store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0],1)

 async def test_long_turn_not_truncated_and_assistant_is_not_evidence(self):
  from paperlife.lifebook.processing import chunks,checked_citations,Citation
  t={'id':1,'role':'user','text':'测试'*4000,'seconds':0}
  parts=list(chunks([t]));self.assertEqual(''.join(x['text'] for part in parts for x in part),t['text'])
  with self.assertRaises(ValueError):checked_citations([Citation(turn_id=2,quote='这句没有依据')],[t],'session')

 async def test_finished_session_missing_job_recovers_and_keeps_full_turn(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import recover_interrupted,chunks
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试','');s=store.create_session(p['id'],1,'test')
   original='完整转写'*4300+'末尾不能丢'
   store.add_turn(s['id'],'user',original,0);store.finish_session(s['id'],'ended')
   self.assertEqual(store.turns(s['id'])[0]['text'],original)
   recover_interrupted(store)
   with store.db() as db:job=dict(db.execute('SELECT * FROM writing_jobs').fetchone())
   result=''.join(t['text'] for t in json.loads(job['snapshot'])['materials'])
   self.assertEqual(result,original)

class ModelJsonTests(unittest.TestCase):
 def test_missing_final_brackets_repaired_without_changing_content(self):
  from paperlife.lifebook.generation import parse_output
  raw='{"stories":[{"title":"测试","paragraphs":[{"text":"原文不变","citations":[{"turn_id":1,"quote":"原文不变"}]}]}'
  self.assertEqual(parse_output(raw)['stories'][0]['paragraphs'][0]['text'],'原文不变')
 def test_truncated_string_or_mismatched_brackets_rejected(self):
  from paperlife.lifebook.generation import parse_output
  for raw in ['{"items":[{"title":"没有结束','{"items":[}', '{"items":']:
   with self.assertRaises((ValueError,json.JSONDecodeError)):parse_output(raw)

class ShortAnswerTests(unittest.TestCase):
 def test_short_answer_keeps_question_context_and_exact_evidence(self):
  from paperlife.lifebook.store import Store
  from paperlife.lifebook.processing import enqueue,Citation,checked_citations
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp));p=store.create_profile(1,'测试','');s=store.create_session(p['id'],1,'test')
   store.add_turn(s['id'],'assistant','这是虚构测试。老师叫什么名字？',0)
   store.add_turn(s['id'],'user','甲乙',2)
   store.finish_session(s['id'],'ended');enqueue(store,s['id'])
   with store.db() as db:turns=json.loads(db.execute('SELECT snapshot FROM processing_jobs').fetchone()['snapshot'])
   self.assertIn('老师叫什么名字',turns[0]['context'])
   evidence=checked_citations([Citation(turn_id=turns[0]['id'],quote='甲乙')],turns,s['id'])
   self.assertEqual(evidence[0]['quote'],'甲乙')
 def test_short_fragment_of_long_turn_is_not_sufficient_evidence(self):
  from paperlife.lifebook.processing import Citation,checked_citations
  with self.assertRaises(ValueError):checked_citations([Citation(turn_id=1,quote='甲乙')],[{'id':1,'text':'甲乙是人工虚构测试名称。','seconds':0}],'s')
