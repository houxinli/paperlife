"""专属入口仅能读取本人已确认成果和采访，不获得编辑或跨人权限。"""
import tempfile,unittest,wave
from pathlib import Path
from fastapi.testclient import TestClient
from paperlife.main import create_app


class NarratorReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(Path(self.tmp.name),start_worker=False)
        self.app.state.auth.create_user('owner','fictional-password-only','admin')
        self.client=TestClient(self.app);self.store=self.app.state.store
        login=self.client.post('/api/auth/login',json={'username':'owner','password':'fictional-password-only'}).json()
        self.owner={'Authorization':'Bearer '+login['session_id']}
        self.a=self.store.create_profile(1,'虚构甲','');self.b=self.store.create_profile(1,'虚构乙','')
        self.link=self.store.create_link(self.a['id']);self.h={'X-Life-Link':self.link['token']}
        self.sa=self.store.create_session(self.a['id'],1,'虚构童年');self.sb=self.store.create_session(self.b['id'],1,'另一个人的访谈')
        self.store.add_turn(self.sa['id'],'user','虚构的原始发言',2)
        self.store.finish_session(self.sa['id'],'ended')

    def tearDown(self):self.client.close();self.tmp.cleanup()

    def test_guest_history_is_scoped_readonly_and_paginated(self):
        r=self.client.get('/api/life/guest/history?limit=1',headers=self.h)
        self.assertEqual(r.status_code,200)
        self.assertEqual([s['id'] for s in r.json()['sessions']],[self.sa['id']])
        self.assertFalse(r.json()['has_more'])
        self.assertIn('no-store',r.headers['cache-control'])
        detail='/api/life/guest/history/'+self.sa['id']
        self.assertEqual(self.client.get(detail,headers=self.h).json()['turns'][0]['text'],'虚构的原始发言')
        self.assertEqual(self.client.get('/api/life/guest/history/'+self.sb['id'],headers=self.h).status_code,404)
        self.assertEqual(self.client.put(detail,headers=self.h,json={'edited_text':'不能改'}).status_code,405)
        self.assertEqual(self.client.get('/api/life/guest/history?limit=1000',headers=self.h).status_code,422)

    def test_only_confirmed_results_and_no_other_profile_are_visible(self):
        for profile,status,title in [(self.a,'confirmed','本人确认稿'),(self.a,'draft','秘密未审草稿'),(self.b,'confirmed','其他人的确认稿')]:
            self.store.add_item(profile['id'],{'kind':'story','title':title,'body':title,'status':status,'source_ids':[]})
        r=self.client.get('/api/life/guest/book',headers=self.h)
        self.assertEqual(r.status_code,200)
        self.assertEqual([i['title'] for i in r.json()['items']],['本人确认稿'])
        self.assertEqual(r.json()['pending_count'],1)
        self.assertNotIn('秘密未审草稿',r.text);self.assertNotIn('其他人的确认稿',r.text)
        normal=self.client.get('/api/life/profiles/'+self.a['id']+'/book',headers=self.owner)
        self.assertEqual(normal.json(),r.json())

    def test_audio_requires_valid_link_and_matching_profile(self):
        for session in (self.sa,self.sb):
            with wave.open(str(self.store.audio_path(session['id'])),'wb') as w:
                w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*10)
        path='/api/life/guest/history/'+self.sa['id']+'/audio'
        self.assertEqual(self.client.get(path,headers=self.h).status_code,200)
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get('/api/life/guest/history/'+self.sb['id']+'/audio',headers=self.h).status_code,404)
        with self.store.db() as db:db.execute('UPDATE narrator_links SET revoked=1 WHERE id=?',(self.link['id'],))
        self.assertEqual(self.client.get(path,headers=self.h).status_code,401)
        self.assertEqual(self.client.get('/api/life/guest/book',headers=self.h).status_code,401)

    def test_confirmed_chapter_reading_does_not_expose_full_generation_snapshot(self):
        import asyncio
        from paperlife.lifebook import chapters, editor_jobs
        c=chapters.create(self.store,self.a['id'],'虚构章节','编辑私有约定')
        editor_jobs.enqueue_chapter(self.store,self.a['id'],c['id'],[{'kind':'session','id':self.sa['id']}],1)
        async def fake(stage,payload):
            u=payload['materials'][0]
            if stage=='outline':out={'outline':[{'title':'虚构小节'}]}
            elif stage=='audit':out={'ok':True,'issues':[]}
            elif stage=='polish':out=payload['draft']
            else:out={'title':'虚构章节','paragraphs':[{'text':'虚构的原始发言','citations':[{'unit_id':u['unit_id'],'quote':'虚构的原始发言'}]}]}
            return {'output':out,'metadata':{}}
        asyncio.run(editor_jobs.process_one(self.store,fake))
        before=self.client.get('/api/life/guest/book',headers=self.h).json()
        self.assertEqual(before['chapters'],[])
        v=chapters.detail(self.store,c['id'])['versions'][0]
        chapters.confirm(self.store,c['id'],v['id'],0,1)
        r=self.client.get('/api/life/guest/book',headers=self.h)
        self.assertEqual(len(r.json()['chapters']),1)
        self.assertNotIn('sources',r.json()['chapters'][0])
        self.assertNotIn('metadata',r.json()['chapters'][0])
        self.assertNotIn('编辑私有约定',r.text)

    def test_normal_history_requires_membership(self):
        self.assertEqual(self.client.get('/api/life/profiles/'+self.a['id']+'/history',headers=self.owner).status_code,200)
        self.client.post('/api/auth/logout',headers=self.owner)
        self.assertEqual(self.client.get('/api/life/profiles/'+self.a['id']+'/history').status_code,401)
