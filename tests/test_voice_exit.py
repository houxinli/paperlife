"""Fictional protocol streams cover voice/button/disconnect lifecycle and saved tails."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperlife.lifebook.prompt import session_config
from paperlife.lifebook.realtime import interview
from paperlife.lifebook.store import Store
from test_realtime_rounds import RecordingClient, ScriptedProvider, provider_frame


class DrainingProvider(ScriptedProvider):
    async def send(self, data):
        import struct
        event = struct.unpack_from('>I', data, 4)[0]
        if event == 501:
            self.sent.append(event)
            for kind, payload in self.events:
                await self.queue.put(provider_frame(kind, payload))
        else:
            await super().send(data)
        if event == 102:
            await self.queue.put(provider_frame(550, {'content': '虚构收尾', 'reply_id': 'tail'}))
            await self.queue.put(provider_frame(559, {'reply_id': 'tail'}))
            await self.queue.put(provider_frame(152, {}))


class VoiceExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.store.configure({'api_key': 'fictional'})
        self.profile = self.store.create_profile(1, '虚构讲述者', '')
        self.session = self.store.create_session(self.profile['id'], 1, '虚构日常')

    async def run_stream(self, events, client=None, drains=False):
        remote = (DrainingProvider if drains else ScriptedProvider)(events)
        client = client or RecordingClient()
        with patch('paperlife.lifebook.realtime.connect', return_value=remote):
            await asyncio.wait_for(interview(client, self.store, self.session, self.profile), 5)
        return remote, client

    def test_provider_configuration_enables_actual_exit_signal(self):
        config = session_config(self.profile, '虚构日常')
        self.assertTrue(config['dialog']['extra'].get('enable_user_query_exit'))

    async def test_final_stop_ends_and_persists_original_tail_before_enqueue(self):
        text = '今天就聊到这里吧。'
        remote, client = await self.run_stream([
            (450, {'question_id': 'q1'}),
            (451, {'results': [{'text': text, 'is_interim': False}]}),
        ], drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')
        messages = [e for e in client.messages if isinstance(e, dict)]
        ending = next(e for e in messages if e['type'] == 'ending')
        self.assertEqual(ending['reason'], 'narrator_request')
        self.assertLess(messages.index(ending), next(i for i, e in enumerate(messages) if e['type'] == 'ended'))
        self.assertEqual(remote.sent.count(102), 1)
        self.assertEqual(remote.sent.count(501), 1)
        turns = self.store.turns(self.session['id'])
        self.assertEqual([t['text'] for t in turns], [text, '虚构收尾'])
        with self.store.db() as db:
            jobs = db.execute('SELECT snapshot FROM processing_jobs WHERE session=?', (self.session['id'],)).fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]['snapshot'])[0]['text'], text)

    async def test_provider_exit_signal_ends_without_lexical_match(self):
        remote, client = await self.run_stream([
            (451, {'results': [{'text': '我该去做饭喽', 'is_interim': False}]}),
            (359, {'reply_id': 'bye', 'status_code': '20000002'}),
        ], drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')
        self.assertEqual(remote.sent.count(102), 1)
        self.assertEqual(sum(e.get('type') == 'ending' for e in client.messages if isinstance(e, dict)), 1)

    async def test_negatives_reported_speech_and_hypotheticals_never_trigger_exit(self):
        for text in ['不要停，继续聊', '别结束采访', '如果我说今天就到这里呢',
                     '他当时说，今天就到这里吧。', '我昨天说过“结束采访”。', '我不是说结束采访']:
            with self.subTest(text=text):
                _, client = await self.run_stream([
                    (450, {'question_id': 'q1'}),
                    (451, {'results': [{'text': text, 'is_interim': False}]}),
                    (359, {'status_code': '20000002'}),
                ])
                self.assertFalse(any(e.get('type') == 'ending' for e in client.messages if isinstance(e, dict)))

    async def test_interim_stop_and_name_are_archived_without_control_effects_even_at_asr_end(self):
        for text in ['结束采访', '叫我小林']:
            _, client = await self.run_stream([
                (451, {'results': [{'text': text, 'is_interim': True}]}), (459, {}),
            ])
            self.assertFalse(any(e.get('type') == 'ending' for e in client.messages if isinstance(e, dict)))
            self.assertEqual(self.store.preferences(self.profile['id']), [])
            self.assertEqual(self.store.profile(self.profile['id'], 1).get('interviewer_name_asked', 0), 0)

    async def test_client_disconnect_during_voice_end_still_saves_ended_session(self):
        class DisconnectOnEnding(RecordingClient):
            async def send_json(self, event):
                if event['type'] == 'ending':
                    raise ConnectionError('fictional disconnected browser')
                await super().send_json(event)
        remote, client = await self.run_stream([
            (451, {'results': [{'text': '结束采访', 'is_interim': False}]}),
        ], client=DisconnectOnEnding(), drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')
        self.assertEqual(self.store.turns(self.session['id'])[0]['text'], '结束采访')
        self.assertEqual(remote.sent.count(102), 1)

    def test_stop_vocabulary_is_direct_and_excludes_conditional_clauses(self):
        from paperlife.lifebook.preferences import requests_exit
        for text in ['结束采访', '请结束这次采访', '今天就到这里吧', '我不想聊了', '先停一下', '我想休息一下', '结束吧']:
            with self.subTest(text=text):
                self.assertTrue(requests_exit(text))
        for text in ['要是累了，结束采访', '假设现在就停一下', '我说的是不要结束采访', '不必结束', '不要停']:
            with self.subTest(text=text):
                self.assertFalse(requests_exit(text))

    def test_quitting_is_not_confused_with_negated_stopping_or_cross_clause_words(self):
        from paperlife.lifebook.preferences import requests_exit,exit_is_vetoed
        for text in ['我不想继续聊了','不要再问了，结束采访','不用问了，今天就到这里吧']:
            with self.subTest(text=text):
                self.assertTrue(requests_exit(text));self.assertFalse(exit_is_vetoed(text))
        for text in ['不要结束采访，我们继续聊','结束采访，不对，继续聊吧','换个话题吧']:
            with self.subTest(text=text):
                self.assertFalse(requests_exit(text));self.assertTrue(exit_is_vetoed(text))

    async def test_provider_exit_waits_for_final_transcript(self):
        _,client=await self.run_stream([
            (450,{'question_id':'q1'}),
            (451,{'results':[{'text':'结束采访','is_interim':True}]}),
            (359,{'question_id':'q1','status_code':'20000002'}),
        ])
        self.assertFalse(any(e.get('type')=='ending' for e in client.messages if isinstance(e,dict)))

    async def test_early_provider_exit_is_vetoed_by_final_correction(self):
        _,client=await self.run_stream([
            (450,{'question_id':'q1'}),
            (451,{'results':[{'text':'结束采访','is_interim':True}]}),
            (359,{'question_id':'q1','status_code':'20000002'}),
            (451,{'results':[{'text':'不要结束采访，我们继续聊。','is_interim':False}]}),
        ])
        self.assertFalse(any(e.get('type')=='ending' for e in client.messages if isinstance(e,dict)))

    async def test_early_provider_exit_is_honored_after_nonlexical_final(self):
        _,client=await self.run_stream([
            (450,{'question_id':'q1'}),
            (451,{'results':[{'text':'我该去','is_interim':True}]}),
            (359,{'question_id':'q1','status_code':'20000002'}),
            (451,{'results':[{'text':'我该去做饭喽','is_interim':False}]}),
        ],drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'],'ended')
        self.assertEqual(sum(e.get('type')=='ending' for e in client.messages if isinstance(e,dict)),1)

    async def test_button_and_final_voice_stop_share_one_finish_and_archive_late_final(self):
        class StopClient(RecordingClient):
            def __init__(self):
                super().__init__()
                self.sent_stop = False
            async def receive(self):
                if not self.sent_stop:
                    self.sent_stop = True
                    return {'type': 'websocket.receive', 'text': '{"type":"stop"}'}
                await asyncio.Future()
        remote, client = await self.run_stream([
            (451, {'results': [{'text': '今天就到这里吧', 'is_interim': False}]}),
            (359, {'status_code': '20000002'}),
        ], client=StopClient(), drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')
        self.assertEqual(remote.sent.count(102), 1)
        self.assertEqual(sum(e.get('type') == 'ending' for e in client.messages if isinstance(e, dict)), 1)
        self.assertEqual(self.store.turns(self.session['id'])[0]['text'], '今天就到这里吧')

    async def test_disconnected_browser_does_not_skip_upstream_finish_after_final_stop(self):
        class GoneClient(RecordingClient):
            ended = False
            async def send_json(self, event):
                if self.ended:
                    raise ConnectionError('fictional browser gone')
                if event['type'] == 'ending':
                    self.ended = True
                await super().send_json(event)
        remote, _ = await self.run_stream([
            (451, {'results': [{'text': '结束采访', 'is_interim': False}]}),
            (359, {'status_code': '20000002'}),
        ], client=GoneClient(), drains=True)
        self.assertEqual(remote.sent.count(102), 1)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')
        self.assertEqual(self.store.turns(self.session['id'])[-1]['text'], '虚构收尾')

    async def test_disconnection_while_forwarding_final_transcript_cannot_hide_stop_intent(self):
        class TranscriptDisconnect(RecordingClient):
            async def send_json(self, event):
                if event['type'] == 'transcript':
                    raise ConnectionError('fictional browser gone')
                await super().send_json(event)
        _, _ = await self.run_stream([
            (451, {'results': [{'text': '结束采访', 'is_interim': False}]}),
        ], client=TranscriptDisconnect(), drains=True)
        self.assertEqual(self.store.session(self.session['id'])['status'], 'ended')

