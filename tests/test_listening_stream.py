"""完整录音、静默、最终识别排空与中断均使用虚构ASR。"""
import asyncio
import json
import tempfile
import unittest
import wave
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch
from paperlife.lifebook.store import Store


class Client:
    def __init__(self,messages): self.messages=messages; self.sent=[]; self.ready=asyncio.Event()
    async def send_json(self,event):
        self.sent.append(event)
        if event['type']=='ready': self.ready.set()
    async def receive(self):
        await self.ready.wait()
        if self.messages: return self.messages.pop(0)
        await asyncio.Future()
    async def send_bytes(self,_): raise AssertionError('旁听永不输出音频')


class Provider:
    def __init__(self,fail=False): self.final=asyncio.Event();self.audio=[];self.fail=fail
    async def send_audio(self,pcm,final=False):
        self.audio.append(pcm)
        if final: self.final.set()
    async def events(self):
        await self.final.wait()
        yield {'segments':[{'id':'s1','speaker_id':'voice1','text':'停止吧，先聊虚构小镇。','start_ms':0,'end_ms':100}], 'final':False}
        if self.fail: raise ValueError('synthetic private upstream error')
        await asyncio.sleep(.02)
        yield {'segments':[{'id':'s1','speaker_id':'voice1','text':'停止吧，先聊虚构小镇和虚构学校。','start_ms':0,'end_ms':100}], 'final':True}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.tmp.name));self.p=self.store.create_profile(1,'虚构主体','')
        self.s=self.store.create_session(self.p['id'],1,'虚构通话')
        from paperlife.lifebook.listening_store import initialize
        initialize(self.store,self.s['id'],[]);self.s=self.store.session(self.s['id'])
    async def asyncTearDown(self): self.tmp.cleanup()

    async def run_stream(self,fail=False,disconnect=False,authorized=True):
        from paperlife.lifebook.listening_stream import listen
        provider=Provider(fail)
        @asynccontextmanager
        async def connect(_): yield provider
        client=Client([{'type':'websocket.receive','bytes':b'\0\1'*1600},
            {'type':'websocket.disconnect'} if disconnect else {'type':'websocket.receive','text':json.dumps({'type':'stop'})}])
        with patch('paperlife.lifebook.listening_stream.connect_asr',connect):
            await asyncio.wait_for(listen(client,self.store,self.s,self.p,authorize=lambda:authorized),2)
        return client,provider

    async def test_final_snapshot_settles_before_ended_audio_complete_and_no_preferences(self):
        from paperlife.lifebook.listening_store import detail
        client,provider=await self.run_stream()
        d=detail(self.store,self.s['id'])
        self.assertEqual(d['recognition_status'],'complete')
        self.assertIn('虚构学校',d['segments'][0]['text'])
        self.assertEqual(self.store.session(self.s['id'])['status'],'ended')
        with wave.open(str(self.store.audio_path(self.s['id']))) as f: self.assertEqual(f.getnframes(),1600)
        self.assertEqual(self.store.preferences(self.p['id']),[])
        self.assertEqual(client.sent[-1]['type'],'ended')
        self.assertEqual(client.sent[-2]['recognition_status'],'complete')

    async def test_error_preserves_wav_partial_and_sanitizes(self):
        from paperlife.lifebook.listening_store import detail
        client,_=await self.run_stream(fail=True)
        d=detail(self.store,self.s['id'])
        self.assertEqual(d['recognition_status'],'incomplete')
        self.assertTrue(d['segments'])
        self.assertTrue(self.store.audio_path(self.s['id']).exists())
        self.assertNotIn('private upstream',json.dumps(client.sent))
        self.assertEqual(self.store.session(self.s['id'])['status'],'interrupted')

    async def test_disconnect_drains_final_and_keeps_interrupted_status(self):
        from paperlife.lifebook.listening_store import detail
        await self.run_stream(disconnect=True)
        self.assertEqual(self.store.session(self.s['id'])['status'],'interrupted')
        self.assertEqual(detail(self.store,self.s['id'])['recognition_status'],'complete')

    async def test_connect_error_preserves_audio_received_before_ready(self):
        from paperlife.lifebook.listening_stream import listen
        from paperlife.lifebook.listening_store import detail
        @asynccontextmanager
        async def connect(_):
            await asyncio.sleep(.02)
            raise ValueError('synthetic credential must not escape')
            yield
        client=Client([{'type':'websocket.receive','bytes':b'\0\1'*1600}]);client.ready.set()
        with patch('paperlife.lifebook.listening_stream.connect_asr',connect):
            await listen(client,self.store,self.s,self.p,authorize=lambda:True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:self.assertEqual(f.getnframes(),1600)
        self.assertEqual(detail(self.store,self.s['id'])['recognition_status'],'incomplete')
        self.assertFalse(any(e['type']=='ready' for e in client.sent))
        self.assertNotIn('credential',json.dumps(client.sent))

    async def test_invalid_pcm_and_rate_limits_keep_prior_audio(self):
        from paperlife.lifebook.listening_stream import listen
        for invalid in (b'x',b'0'*6402):
            provider=Provider()
            @asynccontextmanager
            async def connect(_):yield provider
            client=Client([{'type':'websocket.receive','bytes':b'\0\1'*1600}, {'type':'websocket.receive','bytes':invalid}])
            with patch('paperlife.lifebook.listening_stream.connect_asr',connect):
                await listen(client,self.store,self.s,self.p,authorize=lambda:True)
            with wave.open(str(self.store.audio_path(self.s['id']))) as f:self.assertEqual(f.getnframes(),1600)
            self.assertEqual(self.store.session(self.s['id'])['status'],'interrupted')
            # Next input case uses a new session: original audio cannot be overwritten by retry.
            self.s=self.store.create_session(self.p['id'],1,'虚构通话')
            from paperlife.lifebook.listening_store import initialize
            initialize(self.store,self.s['id'],[]);self.s=self.store.session(self.s['id'])
        provider=Provider()
        @asynccontextmanager
        async def connect(_):yield provider
        client=Client([{'type':'websocket.receive','bytes':b'0'*6400}]*27)
        with patch('paperlife.lifebook.listening_stream.connect_asr',connect):
            await listen(client,self.store,self.s,self.p,authorize=lambda:True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:self.assertEqual(f.getnframes(),80000)
        self.assertEqual(self.store.session(self.s['id'])['status'],'interrupted')

    async def test_twenty_minute_pcm_bound_is_enforced(self):
        from paperlife.lifebook.listening_stream import listen
        provider=Provider()
        @asynccontextmanager
        async def connect(_):yield provider
        client=Client([{'type':'websocket.receive','bytes':b'0'*6400}, {'type':'websocket.receive','bytes':b'0'*6400}])
        with patch('paperlife.lifebook.listening_stream.connect_asr',connect), patch('paperlife.lifebook.listening_stream.MAX_SECONDS',.2):
            await listen(client,self.store,self.s,self.p,authorize=lambda:True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:self.assertEqual(f.getnframes(),3200)
        self.assertEqual(self.store.session(self.s['id'])['status'],'interrupted')

    async def test_unauthorized_never_emits_ready(self):
        client,_=await self.run_stream(authorized=False)
        self.assertFalse(any(e['type']=='ready' for e in client.sent))


if __name__=='__main__':unittest.main()
