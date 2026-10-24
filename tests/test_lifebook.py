import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient


class LifeBookTests(unittest.TestCase):
    def setUp(self):
        from paperlife.lifebook.api import build_router
        self.tmp = tempfile.TemporaryDirectory()
        import sqlite3
        with sqlite3.connect(Path(self.tmp.name) / 'main.db') as db:
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,is_active INTEGER)')
            db.executemany('INSERT INTO users VALUES(?,1)', [(1,),(2,)])
        self.user = SimpleNamespace(id=1, role='admin', username='alice')
        self.app = FastAPI()
        self.app.include_router(build_router(Path(self.tmp.name) / 'main.db', lambda: self.user))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_private_profiles_and_reader_cannot_edit(self):
        p = self.client.post('/life/profiles', json={'name':'外婆','context':'私密回忆'}).json()
        self.assertIn('id', p)
        self.assertEqual(self.client.put('/life/profiles/'+p['id']+'/members/99', json={'role':'reader'}).status_code, 404)
        self.user.id = 2
        self.assertEqual(self.client.get('/life/profiles').json(), [])
        self.assertEqual(self.client.get('/life/profiles/'+p['id']).status_code, 404)
        self.user.id = 1
        self.assertEqual(self.client.put('/life/profiles/'+p['id']+'/members/2', json={'role':'reader'}).status_code, 200)
        self.user.id = 2
        self.assertEqual(self.client.get('/life/profiles/'+p['id']).status_code, 200)
        self.assertEqual(self.client.put('/life/profiles/'+p['id'], json={'name':'changed','context':''}).status_code, 403)
        self.assertEqual(self.client.post('/life/profiles/'+p['id']+'/sessions', json={'topic':'童年'}).status_code, 403)
        self.user.id = 1
        self.client.delete('/life/profiles/'+p['id']+'/members/2')
        self.user.id = 2
        self.assertEqual(self.client.get('/life/profiles/'+p['id']).status_code, 404)

    def test_credentials_never_read_back_and_admin_only(self):
        result = self.client.put('/life/provider', json={'app_id':'123','access_token':'secret-test-value'})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('secret-test-value', self.client.get('/life/provider').text)
        self.user.role = 'user'
        self.assertEqual(self.client.put('/life/provider', json={'app_id':'123','access_token':'x'}).status_code, 403)

    def test_unconfigured_provider_blocks_real_session(self):
        p = self.client.post('/life/profiles', json={'name':'外婆','context':''}).json()
        self.assertEqual(self.client.post('/life/profiles/'+p['id']+'/sessions', json={'topic':'童年'}).status_code, 503)

    def test_raw_transcript_preserved_after_edit(self):
        from paperlife.lifebook.store import Store
        s = Store(Path(self.tmp.name)/'life')
        p = s.create_profile(1, '外婆', '')
        session = s.create_session(p['id'], 1, '童年')
        s.add_turn(session['id'], 'user', '原始转写', 1.5)
        s.finish_session(session['id'], 'ended')
        result = self.client.put('/life/sessions/'+session['id'], json={'title':'童年','edited_text':'校对结果','version':0})
        self.assertEqual(result.status_code, 200)
        data = self.client.get('/life/sessions/'+session['id']).json()
        self.assertEqual(data['turns'][0]['text'], '原始转写')
        self.assertEqual(data['edited_text'], '校对结果')
        self.assertEqual(self.client.put('/life/sessions/'+session['id'], json={'title':'覆盖','edited_text':'冲突','version':0}).status_code, 409)
        self.user.id = 9
        self.assertEqual(self.client.get('/life/sessions/'+session['id']).status_code, 404)
        self.assertEqual(self.client.get('/life/sessions/'+session['id']+'/audio').status_code, 404)


class ProtocolTests(unittest.TestCase):
    def test_official_start_connection_bytes(self):
        from paperlife.lifebook.protocol import encode
        self.assertEqual(encode(1, {}), bytes([17,20,16,0,0,0,0,1,0,0,0,2,123,125]))

    def test_decode_text_and_audio_and_reject_truncated(self):
        from paperlife.lifebook.protocol import decode
        import struct
        payload = b'{"content":"hi"}'
        frame = bytes([17,148,16,0])+struct.pack('>I',550)+struct.pack('>I',1)+b's'+struct.pack('>I',len(payload))+payload
        self.assertEqual(decode(frame)['data']['content'], 'hi')
        with self.assertRaises(ValueError):
            decode(frame[:-1])
        audio = bytes([17,180,0,0])+struct.pack('>I',352)+struct.pack('>I',1)+b's'+struct.pack('>I',2)+b'\0\1'
        self.assertEqual(decode(audio)['data'], b'\0\1')

class RealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bidirectional_protocol_saves_audio_and_text(self):
        import asyncio
        import json
        import struct
        import wave
        from unittest.mock import patch
        from paperlife.lifebook.store import Store
        from paperlife.lifebook.realtime import interview
        from paperlife.lifebook.protocol import decode
        def frame(event, data, kind=9, serial=1):
            payload=json.dumps(data).encode() if serial else data
            return bytes([17,(kind<<4)|4,serial<<4,0])+struct.pack('>I',event)+struct.pack('>I',1)+b's'+struct.pack('>I',len(payload))+payload
        ready = asyncio.Event()
        class Provider:
            def __init__(self):
                self.queue=asyncio.Queue()
                self.sent=[]
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def send(self, data):
                event=struct.unpack_from('>I',data,4)[0];self.sent.append(event)
                if event==1:await self.queue.put(frame(50,{}))
                if event==100:await self.queue.put(frame(150,{}))
                if event==200:
                    await self.queue.put(frame(450,{}))
                    await self.queue.put(frame(451,{'results':[{'text':'请说慢一点。','is_interim':False}]}))
                    await self.queue.put(frame(550,{'content':'跟谁去的？'}))
                    await self.queue.put(frame(559,{}))
                    await self.queue.put(frame(352,b'\0\0'*100,11,0))
                if event==102:
                    await self.queue.put(frame(451,{'results':[{'text':'最后一句','is_interim':False}]}))
                    await self.queue.put(frame(152,{}))
            async def recv(self):return await self.queue.get()
            def __aiter__(self):return self
            async def __anext__(self):return await self.recv()
        class Client:
            def __init__(self):self.events=[];self.sent=False
            async def receive(self):
                if not self.sent:self.sent=True;return {'type':'websocket.receive','bytes':b'\0\0'*320}
                await ready.wait();return {'type':'websocket.receive','text':'{"type":"stop"}'}
            async def send_json(self,event):self.events.append(event)
            async def send_bytes(self,data):ready.set()
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp));store.configure({'app_id':'test','access_token':'test'})
            p=store.create_profile(1,'外婆','');s=store.create_session(p['id'],1,'求学')
            remote=Provider();client=Client()
            with patch('paperlife.lifebook.realtime.connect', return_value=remote):
                await asyncio.wait_for(interview(client,store,s,p),5)
            self.assertEqual(remote.sent[:4],[1,100,501,200])
            self.assertIn(102,remote.sent)
            self.assertEqual(store.session(s['id'])['status'],'ended')
            self.assertEqual([t['text'] for t in store.turns(s['id'])],['请说慢一点。','跟谁去的？','最后一句'])
            with wave.open(str(store.audio_path(s['id']))) as wav:self.assertEqual(wav.getnframes(),320)
            self.assertEqual(client.events[-1]['type'],'ended')
            self.assertTrue(any(e['type']=='preference_saved' for e in client.events))
            self.assertEqual([(p['key'],p['value']) for p in store.preferences(p['id'])],[('pace','slow')])

class LifeBookAccessAndContentTests(LifeBookTests):
    def test_narrator_link_is_scoped_revocable_and_not_account_login(self):
        p=self.client.post('/life/profiles',json={'name':'外婆','context':'不能公开的背景'}).json()
        link=self.client.post('/life/profiles/'+p['id']+'/links',json={}).json()
        self.assertIn('token',link)
        headers={'X-Life-Link':link['token']}
        result=self.client.get('/life/guest',headers=headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['name'],'外婆')
        self.assertNotIn('context',result.json())
        self.user.id=2
        self.assertEqual(self.client.get('/life/profiles/'+p['id'],headers=headers).status_code,404)
        self.assertEqual(self.client.post('/life/profiles/'+p['id']+'/links',json={}).status_code,404)
        self.user.id=1
        self.client.delete('/life/profiles/'+p['id']+'/links/'+str(link['id']))
        self.assertEqual(self.client.get('/life/guest',headers=headers).status_code,401)

    def test_three_content_layers_and_answer_preserve_original(self):
        p=self.client.post('/life/profiles',json={'name':'外婆','context':'原始访谈记载 测试人物拼音'}).json()
        self.client.put('/life/profiles/'+p['id'],json={'name':'外婆','context':'新背景'})
        sources=self.client.get('/life/profiles/'+p['id']+'/sources').json()
        self.assertEqual(sources[0]['text'],'原始访谈记载 测试人物拼音')
        base='/life/profiles/'+p['id']+'/items'
        item=self.client.post(base,json={'kind':'question','title':'姓名用字','body':'测试读音怎么写？','source_ids':[sources[0]['id']]}).json()
        self.assertIn('id',item)
        result=self.client.put('/life/items/'+item['id'],json={'kind':'question','title':'姓名用字','body':'测试读音怎么写？','answer':'暂不确定，保留音记','status':'open','version':0,'source_ids':[sources[0]['id']]}).json()
        self.assertEqual(result['answer'],'暂不确定，保留音记')
        fact=self.client.post(base,json={'kind':'fact','title':'读书','body':'约1960年','source_ids':[]}).json()
        story=self.client.post(base,json={'kind':'story','title':'求学记','body':'第一次离开村庄。','source_ids':[]}).json()
        items=self.client.get(base).json()
        self.assertEqual({i['kind'] for i in items},{'question','fact','story'})
        self.assertEqual(self.client.get('/life/profiles/'+p['id']+'/sources').json()[0]['text'],'原始访谈记载 测试人物拼音')
        self.user.id=2
        self.assertEqual(self.client.get(base).status_code,404)
        self.assertEqual(self.client.put('/life/items/'+fact['id'],json={'kind':'story','title':'x','body':'x','version':0}).status_code,404)

    def test_first_pasted_background_is_archived_before_any_restart(self):
        p=self.client.post('/life/profiles',json={'name':'空档案'}).json()
        self.client.put('/life/profiles/'+p['id'],json={'name':'空档案','context':'第一次粘贴的原文'})
        self.client.put('/life/profiles/'+p['id'],json={'name':'空档案','context':'修改后的背景'})
        sources=self.client.get('/life/profiles/'+p['id']+'/sources').json()
        self.assertEqual(sources[0]['text'],'第一次粘贴的原文')

    def test_drafts_hidden_from_reader_until_human_confirmation(self):
        p=self.client.post('/life/profiles',json={'name':'审核测试'}).json()
        item=self.client.post('/life/profiles/'+p['id']+'/items',json={'kind':'story','title':'未确认草稿','body':'待校对'}).json()
        self.client.put('/life/profiles/'+p['id']+'/members/2',json={'role':'reader'})
        self.user.id=2
        self.assertEqual(self.client.get('/life/profiles/'+p['id']+'/items').json(),[])
        self.assertEqual(self.client.get('/life/items/'+item['id']+'/revisions').status_code,404)
        self.user.id=1
        data={'kind':'story','title':'已校对故事','body':'确认后的文本','status':'confirmed','version':0}
        self.assertEqual(self.client.put('/life/items/'+item['id'],json=data).status_code,200)
        self.user.id=2
        self.assertEqual(self.client.get('/life/profiles/'+p['id']+'/items').json()[0]['body'],'确认后的文本')
        self.assertEqual(self.client.get('/life/items/'+item['id']+'/revisions').status_code,404)

    def test_cross_profile_sources_cannot_be_attached(self):
        p=self.client.post('/life/profiles',json={'name':'A','context':'A原文'}).json()
        q=self.client.post('/life/profiles',json={'name':'B','context':'B原文'}).json()
        source=self.client.get('/life/profiles/'+p['id']+'/sources').json()[0]
        result=self.client.post('/life/profiles/'+q['id']+'/items',json={'kind':'fact','title':'错配','body':'x','source_ids':[source['id']]})
        self.assertEqual(result.status_code,422)

class GuestSessionTests(LifeBookTests):
    def test_guest_ticket_rechecks_revocation_and_binds_profile(self):
        self.client.put('/life/provider',json={'api_key':'test-key'})
        p=self.client.post('/life/profiles',json={'name':'外婆'}).json()
        q=self.client.post('/life/profiles',json={'name':'另一个人'}).json()
        link=self.client.post('/life/profiles/'+p['id']+'/links',json={}).json()
        headers={'X-Life-Link':link['token']}
        s=self.client.post('/life/guest/sessions',headers=headers,json={'topic':'童年','profile':q['id']}).json()
        self.assertEqual(s['profile'],p['id'])
        self.client.delete('/life/profiles/'+p['id']+'/links/'+str(link['id']))
        with self.client.websocket_connect('/life/live',headers={'origin':'http://testserver'}) as ws:
            ws.send_json({'ticket':s['ticket']})
            self.assertEqual(ws.receive_json()['type'],'error')
        self.assertEqual(self.client.post('/life/guest/sessions',headers=headers,json={'topic':'童年'}).status_code,401)

    def test_readers_cannot_answer_or_create_links_and_revision_conflicts(self):
        p=self.client.post('/life/profiles',json={'name':'外婆'}).json()
        item=self.client.post('/life/profiles/'+p['id']+'/items',json={'kind':'question','title':'哪个字？','body':'待核对','status':'open'}).json()
        data={'kind':'question','title':'哪个字？','body':'待核对','answer':'确认的答案','status':'resolved','version':0}
        self.assertEqual(self.client.put('/life/items/'+item['id'],json=data).status_code,200)
        self.assertEqual(self.client.put('/life/items/'+item['id'],json=data).status_code,409)
        history=self.client.get('/life/items/'+item['id']+'/revisions').json()
        self.assertEqual(history[0]['snapshot']['answer'],'')
        self.client.put('/life/profiles/'+p['id']+'/members/2',json={'role':'reader'})
        self.user.id=2
        self.assertEqual(self.client.put('/life/items/'+item['id'],json={**data,'version':1}).status_code,403)
        self.assertEqual(self.client.post('/life/profiles/'+p['id']+'/links',json={}).status_code,403)
