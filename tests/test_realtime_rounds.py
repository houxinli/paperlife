"""虚构协议流验证多轮播放边界；不访问真实语音服务。"""
import asyncio
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from paperlife.lifebook.realtime import interview
from paperlife.lifebook.store import Store


def provider_frame(event, data):
    audio = isinstance(data, bytes)
    payload = data if audio else json.dumps(data).encode()
    return (bytes([17, 180 if audio else 148, 0 if audio else 16, 0])
            + struct.pack('>I', event) + struct.pack('>I', 1) + b's'
            + struct.pack('>I', len(payload)) + payload)


class ScriptedProvider:
    def __init__(self, events):
        self.events = events
        self.queue = asyncio.Queue()
        self.sent = []

    async def __aenter__(self):return self
    async def __aexit__(self, *args):pass
    def __aiter__(self):return self
    async def __anext__(self):return await self.recv()
    async def recv(self):return await self.queue.get()

    async def send(self, data):
        event = struct.unpack_from('>I', data, 4)[0]
        self.sent.append(event)
        if event in (1, 100):
            await self.queue.put(provider_frame(50 if event == 1 else 150, {}))
        if event == 501:
            for event, payload in self.events:
                await self.queue.put(provider_frame(event, payload))
            await self.queue.put(provider_frame(152, {}))


class RecordingClient:
    def __init__(self):self.messages = []
    async def receive(self):await asyncio.Future()
    async def send_json(self, event):self.messages.append(event)
    async def send_bytes(self, data):self.messages.append(data)


class RealtimeRoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_interim_transcript_is_archived_but_never_learned_as_preference(self):
        text='不要问父母'
        remote=ScriptedProvider([(450,{'question_id':'q1'}),
                                 (451,{'results':[{'text':text,'is_interim':True}]})])
        client=RecordingClient()
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));store.configure({'api_key':'fictional'})
            profile=store.create_profile(1,'虚构讲述者','')
            session=store.create_session(profile['id'],1,'虚构测试')
            with patch('paperlife.lifebook.realtime.connect',return_value=remote),patch('paperlife.lifebook.processing.enqueue'):
                await asyncio.wait_for(interview(client,store,session,profile),2)
            self.assertEqual(store.turns(session['id'])[0]['text'],text)
            self.assertEqual(store.preferences(profile['id']),[])
            self.assertFalse(any(isinstance(e,dict) and e['type']=='preference_saved' for e in client.messages))

    async def test_tts_boundaries_precede_audio_even_when_text_arrives_later(self):
        events = []
        pcm = b'\x01\x00' * 24
        for i in range(3):
            ids = {'question_id':f'q{i}', 'reply_id':f'r{i}'}
            events.extend([(450, {'question_id':ids['question_id']}), (459, {}),
                           (350, {**ids, 'tts_type':'default', 'text':'虚构采访问题'}),
                           (352, pcm), (550, {**ids, 'content':'虚构采访问题'}),
                           (351, ids), (350, {**ids, 'tts_type':'default', 'text':'虚构后半句'}),
                           (352, pcm), (351, ids), (559, ids), (359, ids)])
        remote = ScriptedProvider(events)
        client = RecordingClient()
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.configure({'app_id':'fictional', 'access_token':'fictional'})
            profile = store.create_profile(1, '虚构讲述者', '')
            session = store.create_session(profile['id'], 1, '虚构童年')
            with self.assertLogs('paperlife.lifebook.realtime', level='INFO') as logs, patch('paperlife.lifebook.realtime.connect', return_value=remote), patch('paperlife.lifebook.processing.enqueue'):
                await asyncio.wait_for(interview(client, store, session, profile), 2)
            starts = [message for message in client.messages if isinstance(message, dict) and message['type'] == 'audio_start']
            self.assertEqual([event['reply_id'] for event in starts], ['r0','r0','r1','r1','r2','r2'])
            for index, message in enumerate(client.messages):
                if isinstance(message, bytes):
                    self.assertEqual(client.messages[index - 1]['type'], 'audio_start')
            self.assertEqual(sum(event['type'] == 'audio_end' for event in client.messages if isinstance(event, dict)), 3)
            self.assertEqual([event.get('question_id') for event in client.messages if isinstance(event, dict) and event['type'] == 'interrupt'], ['q0','q1','q2'])
            self.assertTrue(all(event['source'] == 'text' for event in client.messages if isinstance(event, dict) and event['type'] == 'answer_start'))
            diagnostic = next(event for event in client.messages if isinstance(event, dict) and event['type'] == 'voice_diagnostic')
            self.assertEqual(diagnostic['audio_chunks'], 6)
            self.assertEqual(diagnostic['audio_bytes'], 288)
            self.assertEqual(diagnostic['forwarded_audio_chunks'], 6)
            self.assertEqual(diagnostic['provider_events']['350'], 6)
            self.assertEqual(diagnostic['provider_events']['550'], 3)
            for source in ('虚构采访问题', '虚构讲述者', 'fictional', 'reply_id', 'question_id'):
                self.assertNotIn(source, '\n'.join(logs.output) + json.dumps(diagnostic, ensure_ascii=False))
            self.assertEqual(remote.sent.count(501), 1, 'only the opening request may be injected')
            self.assertEqual(remote.sent.count(100), 1, 'no per-turn session reset')
            with wave.open(str(store.audio_path(session['id'], 'assistant'))) as saved:
                self.assertEqual(saved.readframes(saved.getnframes()), pcm * 6)
            self.assertEqual(len(store.turns(session['id'])), 3)
