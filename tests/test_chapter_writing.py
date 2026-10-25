import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperlife.lifebook.api import build_router
from paperlife.lifebook.store import Store


async def fictional_editor(stage, payload):
    """明确虚构素材的确定性供应商替身；实际执行队列、引用和持久化代码。"""
    units = payload['materials']
    citations = [{'unit_id': u['unit_id'], 'quote': u['text']} for u in units]
    if stage == 'extract':
        output = {'items': [{'kind': 'fact', 'title': '虚构材料', 'body': u['text'],
                             'citations': [c]} for u, c in zip(units, citations)]}
    elif stage == 'outline':
        output = {'outline': [{'title': '先写离家，再写到达'}]}
    elif stage == 'audit':
        output = {'ok': True, 'issues': []}
    elif stage == 'polish':
        output = payload['draft']
    else:
        output = {'title': '虚构旅途', 'paragraphs': [
            {'text': u['text'], 'citations': [c]} for u, c in zip(units, citations)]}
    return {'output': output, 'metadata': {'provider': 'synthetic', 'model': 'fixture',
                                          'prompt_version': 'test', 'prompt_hash': 'test'}}


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.user = SimpleNamespace(id=1, role='admin')
        app = FastAPI()
        app.include_router(build_router(self.root/'auth.db', lambda: self.user))
        self.client = TestClient(app)
        self.store = Store(self.root/'life')
        self.profile = self.store.create_profile(1, '虚构讲述者', '')
        self.pid = self.profile['id']
        self.sessions = []
        for text in ['我带着蓝色饭盒离开虚构镇。', '第二天我到了虚构城的学校。']:
            s = self.store.create_session(self.pid, 1, '虚构旅途')
            self.store.add_turn(s['id'], 'assistant', '这是合成测试的提问，不能作为证据。', 0)
            self.store.add_turn(s['id'], 'user', text, 3.5)
            self.store.finish_session(s['id'], 'ended')
            self.sessions.append(s)
        self.refs = [{'kind': 'session', 'id': s['id']} for s in self.sessions]

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def chapter(self):
        r = self.client.post(f'/life/profiles/{self.pid}/chapters', json={'title': '去外地', 'brief': '保留本人声音'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def run_jobs(self, generator=fictional_editor):
        from paperlife.lifebook.editor_jobs import process_one
        async def run():
            for _ in range(20):
                if not await process_one(self.store, generator):
                    return
            self.fail('队列没有结束')
        asyncio.run(run())

    def generate(self, chapter, refs=None):
        r = self.client.post(f"/life/chapters/{chapter['id']}/generate", json={'sources': refs or self.refs})
        self.assertEqual(r.status_code, 200, r.text)
        self.run_jobs()
        detail = self.client.get(f"/life/chapters/{chapter['id']}").json()
        self.assertTrue(detail['versions'], detail)
        return detail['versions'][0]

    def confirm(self, chapter, version):
        return self.client.post(f"/life/chapters/{chapter['id']}/versions/{version['id']}/confirm",
                                json={'edit_version': version['edit_version']})

    def test_cross_session_draft_provenance_and_explicit_publication(self):
        c = self.chapter()
        v = self.generate(c)
        self.assertEqual(v['status'], 'draft')
        self.assertEqual({e['source_id'] for e in v['evidence']}, {s['id'] for s in self.sessions})
        self.assertTrue(all(e['turn_id'] and e['seconds'] == 3.5 for e in v['evidence']))
        book = self.client.get(f'/life/profiles/{self.pid}/book').json()
        self.assertEqual(book['chapters'], [])
        self.assertEqual(self.confirm(c, v).status_code, 200)
        self.assertEqual(len(self.client.get(f'/life/profiles/{self.pid}/book').json()['chapters']), 1)

    def test_correction_new_version_does_not_overwrite_confirmed_body_or_original(self):
        c = self.chapter(); old = self.generate(c)
        self.assertEqual(self.confirm(c, old).status_code, 200)
        sid = self.sessions[0]['id']
        changed = self.client.put(f'/life/profiles/{self.pid}/materials/session/{sid}',
                                 json={'text': '我带着绿色饭盒离开虚构镇。', 'version': 0})
        self.assertEqual(changed.status_code, 200, changed.text)
        new = self.generate(c)
        self.assertIn('绿色', new['body']); self.assertNotEqual(old['id'], new['id'])
        book = self.client.get(f'/life/profiles/{self.pid}/book').json()
        self.assertIn('蓝色', book['chapters'][0]['body'])
        self.assertIn('蓝色', self.store.turns(sid)[1]['text'])
        edited_citations = [e for e in new['evidence'] if e['source_id'] == sid]
        self.assertTrue(all(e['revision'] == 1 and e['seconds'] is None for e in edited_citations))
        self.assertEqual(self.confirm(c, new).status_code, 200)
        detail = self.client.get(f"/life/chapters/{c['id']}").json()
        prior = next(x for x in detail['versions'] if x['id'] == old['id'])
        self.assertEqual(prior['body'], old['body']); self.assertEqual(prior['status'], 'confirmed')

    def test_stale_draft_rejected_on_confirm_and_draft_edit_uses_optimistic_lock(self):
        c = self.chapter(); v = self.generate(c)
        path = f"/life/chapters/{c['id']}/versions/{v['id']}"
        r = self.client.put(path, json={'title': v['title'], 'body': v['body']+'\n人工校订。', 'edit_version': 0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.put(path, json={'title': '过时修改', 'body': '冲突', 'edit_version': 0}).status_code, 409)
        self.client.put(f"/life/profiles/{self.pid}/materials/session/{self.sessions[0]['id']}", json={'text': '新校订素材。', 'version': 0})
        self.assertEqual(self.confirm(c, r.json()).status_code, 409)
        self.assertEqual(self.client.get(f'/life/profiles/{self.pid}/book').json()['chapters'], [])

    def test_confirmed_version_immutable_and_repeated_job_completion_is_idempotent(self):
        c = self.chapter(); v = self.generate(c)
        self.assertEqual(self.confirm(c, v).status_code, 200)
        r = self.client.put(f"/life/chapters/{c['id']}/versions/{v['id']}", json={'title': '覆盖', 'body': '覆盖', 'edit_version': 0})
        self.assertEqual(r.status_code, 409)
        self.run_jobs()
        self.assertEqual(len(self.client.get(f"/life/chapters/{c['id']}").json()['versions']), 1)

    def test_cross_profile_sources_and_readers_drafts_are_denied(self):
        c = self.chapter()
        other = self.store.create_profile(2, '另一虚构人物', '')
        s = self.store.create_session(other['id'], 2, '私密测试')
        self.store.add_turn(s['id'], 'user', '他人的虚构资料。', 0); self.store.finish_session(s['id'], 'ended')
        self.assertEqual(self.client.post(f"/life/chapters/{c['id']}/generate", json={'sources': [{'kind': 'session', 'id': s['id']}]}).status_code, 422)
        v = self.generate(c)
        with self.store.db() as db:
            db.execute('INSERT INTO members VALUES(?,?,?)', (self.pid, 2, 'reader'))
        self.user.id = 2
        self.assertEqual(self.client.get(f"/life/chapters/{c['id']}").status_code, 404)
        self.assertEqual(self.client.get(f'/life/profiles/{self.pid}/chapters').json()['chapters'], [])
        self.assertEqual(self.confirm(c, v).status_code, 403)
        self.assertEqual(self.client.get(f'/life/profiles/{self.pid}/writing-jobs').status_code, 403)

    def test_imported_source_corrections_retain_original_and_snapshot(self):
        p = self.store.create_profile(1, '虚构资料人物', '我在虚构街的书店工作。')
        with self.store.db() as db:
            source = dict(db.execute('SELECT * FROM sources WHERE profile=?', (p['id'],)).fetchone())
        url = f"/life/profiles/{p['id']}/materials/source/{source['id']}"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200, first.text)
        updated = self.client.put(url, json={'text': '我在虚构街的文具店工作。', 'version': 0})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['original_text'], source['text'])
        self.assertEqual(updated.json()['revision'], 1)
        self.assertEqual(self.client.put(url, json={'text': '覆盖', 'version': 0}).status_code, 409)

    def test_invented_citation_never_commits_chapter(self):
        c = self.chapter()
        self.client.post(f"/life/chapters/{c['id']}/generate", json={'sources': self.refs})
        async def bad(stage, payload):
            r = await fictional_editor(stage, payload)
            if stage == 'write': r['output']['paragraphs'][0]['citations'][0]['quote'] = '凭空编造的引文'
            return r
        self.run_jobs(bad)
        self.assertEqual(self.client.get(f"/life/chapters/{c['id']}").json()['versions'], [])

    def test_correction_during_generation_produces_stale_draft_without_publishing(self):
        c = self.chapter()
        self.client.post(f"/life/chapters/{c['id']}/generate", json={'sources': self.refs})
        async def edit_during_write(stage, payload):
            if stage == 'write':
                with self.store.db() as db:
                    db.execute('UPDATE sessions SET version=version+1,edited_text=? WHERE id=?', ('已校订的新素材。', self.sessions[0]['id']))
            return await fictional_editor(stage, payload)
        self.run_jobs(edit_during_write)
        v = self.client.get(f"/life/chapters/{c['id']}").json()['versions'][0]
        self.assertTrue(v['stale']); self.assertEqual(self.confirm(c, v).status_code, 409)

    def test_two_drafts_cannot_both_replace_same_published_version(self):
        c = self.chapter(); first = self.generate(c); second = self.generate(c)
        self.assertEqual(self.confirm(c, second).status_code, 200)
        self.assertEqual(self.confirm(c, first).status_code, 409)
        book = self.client.get(f'/life/profiles/{self.pid}/book').json()
        self.assertEqual(book['chapters'][0]['id'], second['id'])

    def test_failed_audit_is_rewritten_on_retry_and_never_published(self):
        c = self.chapter()
        response = self.client.post(f"/life/chapters/{c['id']}/generate", json={'sources': self.refs})
        async def reject(stage, payload):
            if stage == 'audit':
                return {'output': {'ok': False, 'issues': [{'reason': '合成审核失败'}]}, 'metadata': {}}
            return await fictional_editor(stage, payload)
        self.run_jobs(reject)
        self.assertEqual(self.client.get(f"/life/chapters/{c['id']}").json()['versions'], [])
        with self.store.db() as db:
            db.execute('UPDATE writing_jobs SET next_run=0 WHERE id=?', (response.json()['id'],))
        calls = []
        async def recover(stage, payload):
            calls.append(stage)
            return await fictional_editor(stage, payload)
        self.run_jobs(recover)
        self.assertIn('write', calls); self.assertNotIn('outline', calls)
        self.assertEqual(len(self.client.get(f"/life/chapters/{c['id']}").json()['versions']), 1)
        self.assertEqual(self.client.get(f'/life/profiles/{self.pid}/book').json()['chapters'], [])

    def test_extraction_after_edit_uses_corrected_words_without_ai_questions(self):
        sid = self.sessions[0]['id']
        response = self.client.put(f'/life/profiles/{self.pid}/materials/session/{sid}', json={
            'text': 'AI：您住在不存在的宫殿吗？\n虚构讲述者：我带着绿色饭盒离开虚构镇。', 'version': 0})
        self.assertEqual(response.status_code, 200)
        self.run_jobs()
        with self.store.db() as db:
            facts = [dict(r) for r in db.execute('SELECT * FROM items WHERE profile=?', (self.pid,))]
            self.assertEqual(db.execute('SELECT count(*) FROM chapter_versions').fetchone()[0], 0)
        self.assertEqual(len(facts), 1); self.assertEqual(facts[0]['status'], 'draft')
        self.assertIn('绿色', facts[0]['body']); self.assertNotIn('宫殿', facts[0]['body'])

    def test_model_settings_admin_only_and_no_key_echo(self):
        self.user.role = 'user'
        r = self.client.put('/life/editor-models', json={'writer': {'provider': 'deepseek', 'model': 'test', 'api_key': 'synthetic-secret'}})
        self.assertEqual(r.status_code, 403)
        self.user.role = 'admin'
        r = self.client.put('/life/editor-models', json={'writer': {'provider': 'deepseek', 'model': 'test', 'api_key': 'synthetic-secret'}})
        self.assertEqual(r.status_code, 200, r.text); self.assertNotIn('synthetic-secret', r.text)
        self.assertNotIn('synthetic-secret', self.client.get('/life/editor-models').text)
        self.assertEqual(r.json()['extractor']['provider'], 'doubao_realtime')

    def test_imported_extraction_can_be_reviewed_and_confirmed(self):
        p=self.store.create_profile(1,'虚构文档人物','我在虚构书店工作。')
        with self.store.db() as db:
            source=dict(db.execute('SELECT * FROM sources WHERE profile=?',(p['id'],)).fetchone())
        self.client.put(f"/life/profiles/{p['id']}/materials/source/{source['id']}",json={'text':'我在虚构文具店工作。','version':0})
        self.run_jobs()
        with self.store.db() as db:
            ident=db.execute('SELECT id FROM items WHERE profile=?',(p['id'],)).fetchone()['id']
        item=self.store.item(ident)
        r=self.client.put(f'/life/items/{ident}',json={**item,'status':'confirmed'})
        self.assertEqual(r.status_code,200,r.text)

    def test_stale_extracted_fact_cannot_be_confirmed_after_source_correction(self):
        sid=self.sessions[0]['id']
        url=f'/life/profiles/{self.pid}/materials/session/{sid}'
        self.client.put(url,json={'text':'我在虚构甲校读书。','version':0});self.run_jobs()
        with self.store.db() as db:
            ident=db.execute('SELECT id FROM items WHERE profile=?',(self.pid,)).fetchone()['id']
        item=self.store.item(ident)
        self.client.put(url,json={'text':'我在虚构乙校读书。','version':1})
        r=self.client.put(f'/life/items/{ident}',json={**item,'status':'confirmed'})
        self.assertEqual(r.status_code,409,r.text)
        returned=self.client.get(f'/life/profiles/{self.pid}/items').json()
        self.assertTrue(next(i for i in returned if i['id']==ident)['stale'])
