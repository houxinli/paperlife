"""用真实路由和代理输出核对 v1 契约；供应商、身份和资料均为虚构测试夹具。"""

import asyncio
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from paperlife.lifebook.api import build_router
from paperlife.lifebook.realtime import interview
from paperlife.lifebook.store import Store
from test_realtime_rounds import RecordingClient, ScriptedProvider

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'contracts/conversation-v1.json').read_text())
TYPES = {
    'string': (str,),
    'nullable_string': (str, type(None)),
    'integer': (int,),
    'number': (int, float),
    'boolean': (bool,),
    'array': (list,),
    'object': (dict,),
}


def assert_shape(case, value, shape):
    """只实现本文件 manifest 用到的字段、枚举与边界；不是运行时校验框架。"""
    case.assertIsInstance(value, dict)
    for key, kind in shape['required'].items():
        case.assertIn(key, value)
        case.assertIn(type(value[key]), TYPES[kind], key)
    for key, choices in shape.get('enums', {}).items():
        case.assertIn(value[key], choices, key)
    for key, minimum in shape.get('minimums', {}).items():
        case.assertGreaterEqual(value[key], minimum, key)
    for key, limits in shape.get('lengths', {}).items():
        case.assertGreaterEqual(len(value[key]), limits['minimum'], key)
        case.assertLessEqual(len(value[key]), limits['maximum'], key)
    for key, spec in shape.get('arrays', {}).items():
        if 'maximum' in spec:
            case.assertLessEqual(len(value[key]), spec['maximum'], key)
        for row in value[key]:
            assert_shape(case, row, CONTRACT['responses'][spec['shape']])


class ConversationContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / 'auth.sqlite3'
        app = FastAPI()
        app.include_router(
            build_router(
                Store(Path(db).parent / 'life'), lambda: SimpleNamespace(id=1, role='admin'), lambda uid: uid == 1
            ),
            prefix='/api',
        )
        self.client = TestClient(app)
        self.store = Store(Path(self.tmp.name) / 'life')
        self.client.put('/api/life/provider', json={'api_key': 'fictional'})
        self.profile = self.client.post('/api/life/profiles', json={'name': '虚构讲述者'}).json()
        self.link = self.store.create_link(self.profile['id'])
        self.guest = {'X-Life-Link': self.link['token']}

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def check_response(self, response, shape):
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        assert_shape(self, data, CONTRACT['responses'][shape])
        return data

    def test_member_and_guest_history_details_and_book_have_the_same_minimum_contract(self):
        session = self.store.create_session(self.profile['id'], 1, '虚构日常')
        self.store.add_turn(session['id'], 'user', '这是虚构的原话。', 1.25)
        self.store.finish_session(session['id'], 'ended')
        for status in ['draft', 'confirmed']:
            self.store.add_item(
                self.profile['id'],
                {'kind': 'story', 'title': '虚构故事', 'body': '虚构正文', 'status': status, 'source_ids': []},
            )
        for base, detail, headers in [
            ('/api/life/profiles/' + self.profile['id'], '/api/life/sessions/' + session['id'], {}),
            ('/api/life/guest', '/api/life/guest/history/' + session['id'], self.guest),
        ]:
            history = self.check_response(self.client.get(base + '/history?limit=1', headers=headers), 'history')
            self.assertEqual([row['id'] for row in history['sessions']], [session['id']])
            self.check_response(self.client.get(detail, headers=headers), 'session_detail')
            book = self.check_response(self.client.get(base + '/book', headers=headers), 'book')
            self.assertEqual(len(book['items']), 1)
            self.assertEqual(book['pending_count'], 1)
            maximum = CONTRACT['responses']['history']['arrays']['sessions']['maximum']
            self.assertEqual(
                self.client.get(base + '/history?limit=' + str(maximum + 1), headers=headers).status_code, 422
            )

    def test_start_contract_accepts_each_transport_and_rejects_unknown_or_oversized_input(self):
        path = '/api/life/profiles/' + self.profile['id'] + '/sessions'
        self.assertEqual(self.client.post(path, json={'transport': 'invented'}).status_code, 422)
        too_long = 'x' * (CONTRACT['start_session']['topic_max_length'] + 1)
        self.assertEqual(self.client.post(path, json={'topic': too_long}).status_code, 422)

        async def connected(ws, *args, **kwargs):
            await ws.send_json({'type': 'ready', 'session': args[1]['id']})

        for transport in CONTRACT['start_session']['transports']:
            result = self.check_response(self.client.post(path, json={'transport': transport}), 'session_ticket')
            with patch('paperlife.lifebook.realtime.interview', connected):
                with self.client.websocket_connect(
                    CONTRACT['start_session']['socket_path'], headers={'origin': 'http://testserver'}
                ) as ws:
                    ws.send_json({'ticket': result['ticket']})
                    self.assertEqual(ws.receive_json()['type'], 'ready')
        default = self.check_response(self.client.post(path, json={}), 'session_ticket')
        # 默认 web 票据不能在缺省 Origin 时变成原生认证。
        with self.client.websocket_connect(CONTRACT['start_session']['socket_path']) as ws:
            ws.send_json({'ticket': default['ticket']})
            self.assertEqual(ws.receive_json()['type'], 'error')
        self.check_response(
            self.client.post('/api/life/guest/sessions', json={'transport': 'wechat'}, headers=self.guest),
            'session_ticket',
        )

    async def test_actual_proxy_events_and_pcm_play_through_both_generated_gates(self):
        ids = {'reply_id': 'r1', 'question_id': 'q1'}
        pcm = b'\x00\x40' * 24
        remote = ScriptedProvider(
            [
                (450, {'question_id': 'q1'}),
                (350, ids),
                (352, pcm),
                (550, {**ids, 'content': '虚构前半句'}),
                (351, ids),
                (350, ids),
                (352, pcm),
                (559, ids),
                (359, ids),
                (450, {'question_id': 'q2'}),
                (451, {'results': [{'text': '今天就聊到这里', 'is_interim': False}]}),
            ]
        )
        client = RecordingClient()
        session = self.store.create_session(self.profile['id'], 1, '虚构日常')
        with (
            patch('paperlife.lifebook.realtime.connect', return_value=remote),
            patch('paperlife.lifebook.editor_jobs.enqueue_session'),
        ):
            await asyncio.wait_for(interview(client, self.store, session, self.profile), 2)
        names = set()
        trace = []
        for message in client.messages:
            if isinstance(message, bytes):
                trace.append({'pcm_bytes': len(message)})
                continue
            names.add(message['type'])
            self.assertIn(message['type'], CONTRACT['events'])
            assert_shape(self, message, CONTRACT['events'][message['type']])
            trace.append({'event': message})
        self.assertTrue(
            {
                'ready',
                'interrupt',
                'audio_start',
                'audio_sentence_end',
                'audio_end',
                'answer_start',
                'transcript',
                'ending',
                'ended',
            }
            <= names
        )
        for track, direction in [('user', 'input'), ('assistant', 'output')]:
            with wave.open(str(self.store.audio_path(session['id'], track))) as saved:
                spec = CONTRACT['audio'][direction]
                self.assertEqual(saved.getframerate(), spec['sample_rate'])
                self.assertEqual(saved.getnchannels(), spec['channels'])
                self.assertEqual(saved.getsampwidth(), spec['bytes_per_sample'])
                if track == 'assistant':
                    self.assertEqual(saved.readframes(saved.getnframes()), pcm * 2)
        replay = await asyncio.to_thread(
            subprocess.run,
            ['node', str(ROOT / 'tests/replay_gate.mjs')],
            input=json.dumps(trace),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(
            json.loads(replay.stdout), [{'played_bytes': 96, 'open': False}, {'played_bytes': 96, 'open': False}]
        )

    def test_shape_checks_reject_type_drift_draft_leaks_and_unbounded_history_pages(self):
        invalid = [
            ('history', {'sessions': [], 'has_more': 'false'}),
            ('history', {'sessions': [{}] * 51, 'has_more': False}),
            ('book', {'items': [], 'pending_count': True}),
            (
                'book_item',
                {'id': 'fictional', 'kind': 'story', 'status': 'draft', 'title': '', 'body': '', 'evidence': []},
            ),
        ]
        for name, value in invalid:
            with self.subTest(shape=name), self.assertRaises(AssertionError):
                assert_shape(self, value, CONTRACT['responses'][name])
