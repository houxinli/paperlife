"""采访习惯只能由该档案讲述者或整理者撤销。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from paperlife.lifebook.api import build_router
from paperlife.lifebook.store import Store


class PreferenceAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)
        with sqlite3.connect(self.path/'main.db') as db:
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,is_active INTEGER)')
            db.executemany('INSERT INTO users VALUES(?,1)',[(1,),(2,)])
        self.user=SimpleNamespace(id=1,role='admin')
        app=FastAPI();app.include_router(build_router(self.path/'main.db',lambda:self.user))
        self.client=TestClient(app);self.store=Store(self.path/'life')
        self.p=self.store.create_profile(1,'虚构甲','')
        self.q=self.store.create_profile(1,'虚构乙','')

    def tearDown(self):
        self.client.close();self.tmp.cleanup()

    def test_preferences_require_membership_and_guest_is_bound_to_profile(self):
        base='/life/profiles/'+self.p['id']+'/preferences'
        self.assertEqual(self.client.get(base).json(),[])
        self.user.id=2
        self.assertEqual(self.client.get(base).status_code,404)
        self.user.id=1
        token=self.client.post('/life/profiles/'+self.p['id']+'/links',json={}).json()['token']
        h={'X-Life-Link':token}
        self.user.id=2
        self.assertEqual(self.client.get('/life/guest/preferences',headers=h).json(),[])
        self.assertEqual(self.client.delete('/life/guest/preferences/not-owned',headers=h).status_code,404)
        self.assertEqual(self.client.get('/life/guest/preferences').status_code,401)

    def test_reader_cannot_revoke(self):
        base='/life/profiles/'+self.p['id']
        self.client.put(base+'/members/2',json={'role':'reader'})
        self.user.id=2
        self.assertEqual(self.client.get(base+'/preferences').status_code,200)
        self.assertEqual(self.client.delete(base+'/preferences/anything').status_code,403)

    def test_guest_can_revoke_only_own_preference_and_original_stays(self):
        s=self.store.create_session(self.p['id'],1,'虚构测试')
        t=self.store.add_turn(s['id'],'user','请说慢一点。',1)
        entry=self.store.record_preferences(s['id'],t,'请说慢一点。')[0]
        token=self.client.post('/life/profiles/'+self.q['id']+'/links',json={}).json()['token']
        self.assertEqual(self.client.delete('/life/guest/preferences/'+str(entry['id']),headers={'X-Life-Link':token}).status_code,404)
        token=self.client.post('/life/profiles/'+self.p['id']+'/links',json={}).json()['token']
        h={'X-Life-Link':token}
        self.assertEqual(self.client.get('/life/guest/preferences',headers=h).json()[0]['source_text'],'请说慢一点。')
        self.assertEqual(self.client.delete('/life/guest/preferences/'+str(entry['id']),headers=h).status_code,200)
        self.assertEqual(self.client.get('/life/guest/preferences',headers=h).json(),[])
        self.assertEqual(self.store.turns(s['id'])[0]['text'],'请说慢一点。')
