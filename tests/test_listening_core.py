"""旁听真实路由/数据库测试；所有人物与转写均为虚构。"""
import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from paperlife.lifebook.api import build_router
from paperlife.lifebook.store import Store
from paperlife.lifebook.materials import snapshot, MaterialError


class ListeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.user = SimpleNamespace(id=1, role='admin')
        app = FastAPI(); app.include_router(build_router(self.root/'auth.db', lambda: self.user))
        self.client = TestClient(app); self.store = Store(self.root/'life')
        self.p = self.store.create_profile(1, '虚构主体', '')
        self.store.configure({'api_key': 'synthetic-only'})

    def tearDown(self):
        self.client.close(); self.tmp.cleanup()

    def create(self):
        self.assertEqual(self.client.put('/life/listening/provider', json={'enabled': True}).status_code, 200)
        r = self.client.post(f"/life/profiles/{self.p['id']}/listening", json={'title':'虚构通话', 'consent':True,
            'participants':[{'id':'p1','name':'虚构主体','relationship':'本人','is_subject':True},
                            {'id':'p2','name':'虚构亲友','relationship':'朋友','is_subject':False}]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def finalized(self):
        from paperlife.lifebook.listening_store import save_recognition
        s = self.create(); sid = s['id']
        save_recognition(self.store, sid, [
            {'id':'s1','speaker_id':'v1','text':'我去了虚构小镇。','start_ms':0,'end_ms':1000},
            {'id':'s2','speaker_id':'v2','text':'我一直留在虚构城。','start_ms':1100,'end_ms':2000}], 'complete')
        self.store.finish_session(sid,'ended')
        return s

    def detail(self, sid):
        return self.client.get('/life/listening/sessions/'+sid).json()

    def update(self, sid, version=0, **kw):
        d = self.detail(sid)
        return self.client.put('/life/listening/sessions/'+sid, json={'version':version,
            'participants':d['participants'], 'bindings':{'v1':'p1','v2':'p2'},'overrides':{}, **kw})

    def test_provider_disabled_is_real_and_requires_consent(self):
        r = self.client.get('/life/listening/provider')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['enabled'])
        r = self.client.post(f"/life/profiles/{self.p['id']}/listening", json={'consent':True})
        self.assertEqual(r.status_code, 503)
        self.client.put('/life/listening/provider', json={'enabled':True})
        r = self.client.post(f"/life/profiles/{self.p['id']}/listening", json={'consent':False})
        self.assertEqual(r.status_code, 422)

    def test_original_immutable_versioned_binding_and_single_override(self):
        from paperlife.lifebook.listening_store import save_recognition
        sid = self.finalized()['id']; before = self.detail(sid)
        self.assertIsNone(before['segments'][0]['participant_id'])
        r = self.update(sid, overrides={'s2':{'speaker_id':'v1','text':'我后来留在虚构城。'}})
        self.assertEqual(r.status_code, 200, r.text); after = r.json()
        self.assertEqual(after['session']['version'],1)
        self.assertEqual(after['segments'][1]['participant_id'],'p1')
        self.assertEqual(after['original_segments'],before['original_segments'])
        with self.assertRaises(MaterialError): save_recognition(self.store,sid,[], 'complete')
        self.assertEqual(self.update(sid).status_code,409)
        old = self.client.get(f'/life/listening/sessions/{sid}/versions/0').json()
        self.assertIsNone(old['segments'][0]['participant_id'])
        self.assertEqual(self.update(sid,1,bindings={'v1':'missing'}).status_code,422)
        self.assertEqual(self.update(sid,1,overrides={'missing':{'text':'错误'}}).status_code,422)
        self.assertEqual(self.update(sid,1,overrides={'s1':{'speaker_id':[]}}).status_code,422)

    def test_attribution_and_snapshot_staleness(self):
        from paperlife.lifebook.materials import stale
        from paperlife.lifebook.editor_jobs import enqueue_session, enqueue_chapter
        sid = self.finalized()['id']; refs=[{'kind':'session','id':sid}]
        self.assertIsNone(enqueue_session(self.store,sid))
        with self.assertRaises(MaterialError): snapshot(self.store,self.p['id'],refs)
        self.assertEqual(self.update(sid).status_code,200)
        data = snapshot(self.store,self.p['id'],refs)
        self.assertTrue(data['materials'][0]['speaker']['is_subject'])
        self.assertFalse(data['materials'][1]['speaker']['is_subject'])
        self.assertEqual(data['materials'][1]['speaker']['name'],'虚构亲友')
        self.assertEqual(data['materials'][0]['segment_id'],'s1')
        self.assertEqual(self.update(sid,1,bindings={'v1':'p2','v2':'p1'}).status_code,200)
        self.assertTrue(stale(self.store,self.p['id'],refs,data['source_hash']))

    def test_manual_group_merge_split_and_preference_guard(self):
        sid=self.finalized()['id']
        r=self.update(sid,bindings={'v1':'p1','v2':'p1','manual':'p2'},overrides={'s2':{'speaker_id':'manual'}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['segments'][1]['participant_id'],'p2')
        turn=self.store.add_turn(sid,'user','以后请叫我虚构太阳。',0)
        self.assertEqual(self.store.record_preferences(sid,turn,'以后请叫我虚构太阳。'),[])

    def test_chapter_admission_and_item_chapter_snapshots_become_stale(self):
        from paperlife.lifebook.editor_jobs import process_one
        from test_chapter_writing import fictional_editor
        sid=self.finalized()['id'];refs=[{'kind':'session','id':sid}]
        chapter=self.client.post(f"/life/profiles/{self.p['id']}/chapters",json={'title':'虚构章节'}).json()
        route=f"/life/chapters/{chapter['id']}/generate"
        self.assertEqual(self.client.post(route,json={'sources':refs}).status_code,422)
        self.update(sid)
        self.assertEqual(self.client.post(route,json={'sources':refs}).status_code,200)
        async def run():
            while await process_one(self.store,fictional_editor): pass
        asyncio.run(run())
        detail=self.client.get(f"/life/chapters/{chapter['id']}").json()
        version=detail['versions'][0]
        self.assertFalse(version['stale'])
        items=self.client.get(f"/life/profiles/{self.p['id']}/items").json()
        self.assertTrue(items);self.assertFalse(items[0]['stale'])
        self.assertIn('speaker',items[0]['evidence'][0])
        self.update(sid,1,bindings={'v1':'p2','v2':'p1'})
        detail=self.client.get(f"/life/chapters/{chapter['id']}").json()
        self.assertTrue(detail['versions'][0]['stale'])
        self.assertTrue(self.store.item(items[0]['id'])['stale'])
        confirm=f"/life/chapters/{chapter['id']}/versions/{version['id']}/confirm"
        self.assertEqual(self.client.post(confirm,json={'edit_version':0}).status_code,409)

    def test_live_dispatch_uses_persisted_mode_one_use_and_rechecks_access(self):
        from test_listening_stream import Provider
        s=self.create();provider=Provider()
        @asynccontextmanager
        async def connect(_): yield provider
        with patch('paperlife.lifebook.listening_stream.connect_asr',connect):
            with self.client.websocket_connect('/life/live',headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'ticket':s['ticket'],'mode':'dialogue'})
                self.assertEqual(ws.receive_json()['mode'],'listening')
                ws.send_bytes(b'\0\1'*1600);ws.send_json({'type':'stop'})
                events=[]
                while True:
                    event=ws.receive_json();events.append(event)
                    if event['type']=='ended':break
                self.assertEqual(events[-1]['recognition_status'],'complete')
            with self.client.websocket_connect('/life/live',headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'ticket':s['ticket']});self.assertEqual(ws.receive_json()['type'],'error')
        self.assertEqual(self.detail(s['id'])['recognition_status'],'complete')
        next_session=self.client.post(f"/life/profiles/{self.p['id']}/listening",json={'consent':True}).json()
        with self.store.db() as db:db.execute("UPDATE members SET role='reader' WHERE profile=? AND user=1",(self.p['id'],))
        with self.client.websocket_connect('/life/live',headers={'origin':'http://testserver'}) as ws:
            ws.send_json({'ticket':next_session['ticket']});self.assertEqual(ws.receive_json()['type'],'error')

    def test_legacy_queue_refuses_listening_and_restart_freezes_partial(self):
        from paperlife.lifebook.processing import enqueue,recover_interrupted
        from paperlife.lifebook.listening_store import save_recognition
        sid=self.create()['id']
        save_recognition(self.store,sid,[{'id':'s1','speaker_id':'v1','text':'虚构的半句','start_ms':0,'end_ms':50}],'partial')
        self.store.add_turn(sid,'user','声音组 v1：虚构的半句',0)
        self.store.finish_session(sid,'interrupted')
        self.assertIsNone(enqueue(self.store,sid))
        self.store.finish_session(sid,'active')
        recover_interrupted(self.store)
        self.assertEqual(self.detail(sid)['recognition_status'],'incomplete')
        with self.assertRaises(MaterialError):save_recognition(self.store,sid,[],'partial')

    def test_reader_cross_profile_guest_and_legacy_editor_guards(self):
        sid = self.finalized()['id']; self.update(sid)
        r=self.client.put('/life/sessions/'+sid,json={'title':'x','edited_text':'我去过。','version':1})
        self.assertEqual(r.status_code,422)
        with self.store.db() as db: db.execute('INSERT INTO members VALUES(?,?,?)',(self.p['id'],2,'reader'))
        self.user=SimpleNamespace(id=2,role='user')
        self.assertEqual(self.client.get('/life/listening/sessions/'+sid).status_code,200)
        self.assertEqual(self.update(sid,1).status_code,403)
        self.user=SimpleNamespace(id=3,role='user')
        self.assertEqual(self.client.get('/life/listening/sessions/'+sid).status_code,404)
        link = self.store.create_link(self.p['id'])
        guest={'x-life-link':link['token']}
        self.assertFalse(self.client.get('/life/guest/listening/'+sid,headers=guest).json()['can_edit'])
        self.assertEqual(self.client.put('/life/guest/listening/'+sid,headers=guest,json={}).status_code,405)
        other = self.store.create_profile(4,'另一虚构主体',''); bad=self.store.create_link(other['id'])
        self.assertEqual(self.client.get('/life/guest/listening/'+sid,headers={'x-life-link':bad['token']}).status_code,404)
        history=self.client.get('/life/guest/history',headers=guest).json()
        self.assertEqual(history['sessions'][0]['mode'],'listening')


if __name__ == '__main__': unittest.main()
