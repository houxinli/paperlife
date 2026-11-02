"""完整录音、静默、最终识别排空与中断均使用虚构ASR。"""

import asyncio
import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch
from paperlife.lifebook.store import Store


class Client:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []
        self.ready = asyncio.Event()

    async def send_json(self, event):
        self.sent.append(event)
        if event['type'] == 'ready':
            self.ready.set()

    async def receive(self):
        await self.ready.wait()
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Future()

    async def send_bytes(self, _):
        raise AssertionError('旁听永不输出音频')


class Provider:
    def __init__(self, fail=False):
        self.final = asyncio.Event()
        self.audio = []
        self.fail = fail

    async def send_audio(self, pcm, final=False):
        self.audio.append(pcm)
        if final:
            self.final.set()

    async def events(self):
        await self.final.wait()
        yield {
            'segments': [
                {'id': 's1', 'speaker_id': 'voice1', 'text': '停止吧，先聊虚构小镇。', 'start_ms': 0, 'end_ms': 100}
            ],
            'final': False,
        }
        if self.fail:
            raise ValueError('synthetic private upstream error')
        await asyncio.sleep(0.02)
        yield {
            'segments': [
                {
                    'id': 's1',
                    'speaker_id': 'voice1',
                    'text': '停止吧，先聊虚构小镇和虚构学校。',
                    'start_ms': 0,
                    'end_ms': 100,
                }
            ],
            'final': True,
        }


class ScriptedProvider:
    """先给若干累计临时快照，收到终包后给最终快照。"""

    def __init__(self, partials, final_segments):
        self.partials = partials
        self.final_segments = final_segments
        self.final = asyncio.Event()

    async def send_audio(self, pcm, final=False):
        if final:
            self.final.set()

    async def events(self):
        for segments in self.partials:
            yield {'segments': segments, 'final': False}
            await asyncio.sleep(0)
        await self.final.wait()
        yield {'segments': self.final_segments, 'final': True}


def fictional_segments(count):
    return [
        {'id': f's{i}', 'speaker_id': 'voice1', 'text': f'虚构第{i}句。', 'start_ms': i * 100, 'end_ms': i * 100 + 50}
        for i in range(1, count + 1)
    ]


STOP = {'type': 'websocket.receive', 'text': json.dumps({'type': 'stop'})}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.p = self.store.create_profile(1, '虚构主体', '')
        self.s = self.store.create_session(self.p['id'], 1, '虚构通话')
        from paperlife.lifebook.listening_store import initialize

        initialize(self.store, self.s['id'], [])
        self.s = self.store.session(self.s['id'])

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def new_session(self):
        from paperlife.lifebook.listening_store import initialize

        self.s = self.store.create_session(self.p['id'], 1, '虚构通话')
        initialize(self.store, self.s['id'], [])
        self.s = self.store.session(self.s['id'])

    async def run_stream(self, fail=False, disconnect=False, authorized=True, provider=None):
        from paperlife.lifebook.listening_stream import listen

        provider = provider or Provider(fail)

        @asynccontextmanager
        async def connect(_):
            yield provider

        client = Client(
            [
                {'type': 'websocket.receive', 'bytes': b'\0\1' * 1600},
                {'type': 'websocket.disconnect'}
                if disconnect
                else {'type': 'websocket.receive', 'text': json.dumps({'type': 'stop'})},
            ]
        )
        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            await asyncio.wait_for(listen(client, self.store, self.s, self.p, authorize=lambda: authorized), 2)
        return client, provider

    async def test_final_snapshot_settles_before_ended_audio_complete_and_no_preferences(self):
        from paperlife.lifebook.listening_store import detail

        client, provider = await self.run_stream()
        d = detail(self.store, self.s['id'])
        self.assertEqual(d['recognition_status'], 'complete')
        self.assertIn('虚构学校', d['segments'][0]['text'])
        self.assertEqual(self.store.session(self.s['id'])['status'], 'ended')
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:
            self.assertEqual(f.getnframes(), 1600)
        self.assertEqual(self.store.preferences(self.p['id']), [])
        self.assertEqual(client.sent[-1]['type'], 'ended')
        self.assertEqual(client.sent[-2]['recognition_status'], 'complete')

    async def test_error_preserves_wav_partial_and_sanitizes(self):
        from paperlife.lifebook.listening_store import detail

        client, _ = await self.run_stream(fail=True)
        d = detail(self.store, self.s['id'])
        self.assertEqual(d['recognition_status'], 'incomplete')
        self.assertTrue(d['segments'])
        self.assertTrue(self.store.audio_path(self.s['id']).exists())
        self.assertNotIn('private upstream', json.dumps(client.sent))
        self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')

    async def test_disconnect_drains_final_and_keeps_interrupted_status(self):
        from paperlife.lifebook.listening_store import detail

        await self.run_stream(disconnect=True)
        self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')
        self.assertEqual(detail(self.store, self.s['id'])['recognition_status'], 'complete')

    async def test_connect_error_preserves_audio_received_before_ready(self):
        from paperlife.lifebook.listening_stream import listen
        from paperlife.lifebook.listening_store import detail

        @asynccontextmanager
        async def connect(_):
            await asyncio.sleep(0.02)
            raise ValueError('synthetic credential must not escape')
            yield

        client = Client([{'type': 'websocket.receive', 'bytes': b'\0\1' * 1600}])
        client.ready.set()
        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            await listen(client, self.store, self.s, self.p, authorize=lambda: True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:
            self.assertEqual(f.getnframes(), 1600)
        self.assertEqual(detail(self.store, self.s['id'])['recognition_status'], 'incomplete')
        self.assertFalse(any(e['type'] == 'ready' for e in client.sent))
        self.assertNotIn('credential', json.dumps(client.sent))

    async def test_invalid_pcm_and_rate_limits_keep_prior_audio(self):
        from paperlife.lifebook.listening_stream import listen

        for invalid in (b'x', b'0' * 6402):
            provider = Provider()

            @asynccontextmanager
            async def connect(_):
                yield provider

            client = Client(
                [
                    {'type': 'websocket.receive', 'bytes': b'\0\1' * 1600},
                    {'type': 'websocket.receive', 'bytes': invalid},
                ]
            )
            with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
                await listen(client, self.store, self.s, self.p, authorize=lambda: True)
            with wave.open(str(self.store.audio_path(self.s['id']))) as f:
                self.assertEqual(f.getnframes(), 1600)
            self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')
            # Next input case uses a new session: original audio cannot be overwritten by retry.
            self.s = self.store.create_session(self.p['id'], 1, '虚构通话')
            from paperlife.lifebook.listening_store import initialize

            initialize(self.store, self.s['id'], [])
            self.s = self.store.session(self.s['id'])
        provider = Provider()

        @asynccontextmanager
        async def connect(_):
            yield provider

        client = Client([{'type': 'websocket.receive', 'bytes': b'0' * 6400}] * 27)
        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            await listen(client, self.store, self.s, self.p, authorize=lambda: True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:
            self.assertEqual(f.getnframes(), 80000)
        self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')

    async def test_duration_pcm_bound_is_enforced(self):
        from paperlife.lifebook.listening_stream import listen

        provider = Provider()

        @asynccontextmanager
        async def connect(_):
            yield provider

        client = Client(
            [{'type': 'websocket.receive', 'bytes': b'0' * 6400}, {'type': 'websocket.receive', 'bytes': b'0' * 6400}]
        )
        with (
            patch('paperlife.lifebook.listening_stream.connect_asr', connect),
            patch('paperlife.lifebook.listening_stream.MAX_SECONDS', 0.2),
        ):
            await listen(client, self.store, self.s, self.p, authorize=lambda: True)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:
            self.assertEqual(f.getnframes(), 3200)
        self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')

    async def test_unauthorized_never_emits_ready(self):
        client, _ = await self.run_stream(authorized=False)
        self.assertFalse(any(e['type'] == 'ready' for e in client.sent))

    async def test_partial_snapshots_are_throttled_and_final_is_always_saved(self):
        from paperlife.lifebook import listening_store

        for interval, expected_partials in ((5, 0), (0, 3)):
            with self.subTest(interval=interval):
                self.new_session()
                statuses = []

                def recording(store, sid, segments, status):
                    statuses.append(status)
                    return listening_store.save_recognition(store, sid, segments, status)

                provider = ScriptedProvider([fictional_segments(n) for n in (1, 2, 3)], fictional_segments(4))
                with (
                    patch('paperlife.lifebook.listening_stream.save_recognition', recording),
                    patch('paperlife.lifebook.listening_stream.SNAPSHOT_SECONDS', interval),
                ):
                    await self.run_stream(provider=provider)
                self.assertEqual(statuses.count('partial'), expected_partials)
                self.assertEqual(statuses[-1], 'complete')
                self.assertEqual(len(listening_store.detail(self.store, self.s['id'])['segments']), 4)

    async def test_live_captions_carry_only_recent_segments(self):
        from paperlife.lifebook.listening_store import detail
        from paperlife.lifebook.listening_stream import LIVE_SEGMENTS

        many = fictional_segments(LIVE_SEGMENTS + 10)
        client, _ = await self.run_stream(provider=ScriptedProvider([many], many))
        live = [e for e in client.sent if e['type'] == 'listening_transcript']
        self.assertEqual([len(e['segments']) for e in live], [LIVE_SEGMENTS, LIVE_SEGMENTS])
        self.assertEqual({e['total'] for e in live}, {LIVE_SEGMENTS + 10})
        self.assertEqual(live[0]['segments'][0]['id'], 's11')
        self.assertTrue(live[-1]['final'])
        # 下发裁剪不影响档案里的完整识别。
        self.assertEqual(len(detail(self.store, self.s['id'])['segments']), LIVE_SEGMENTS + 10)

    async def test_access_is_rechecked_once_per_second_and_revocation_still_ends(self):
        from paperlife.lifebook.listening_stream import listen

        calls = []

        def authorize():
            calls.append(time.monotonic())
            return True

        packets = [{'type': 'websocket.receive', 'bytes': b'\0\1' * 320}] * 100
        client = Client(packets + [STOP])

        @asynccontextmanager
        async def connect(_):
            yield Provider()

        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            await asyncio.wait_for(listen(client, self.store, self.s, self.p, authorize=authorize), 2)
        # 100 个 20ms 音频包加上供应商事件，原来每个都查一次库。
        self.assertLessEqual(len(calls), 3)
        self.assertEqual(self.store.session(self.s['id'])['status'], 'ended')

        self.new_session()
        allowed = {'value': True}
        client = Client([{'type': 'websocket.receive', 'bytes': b'\0\1' * 320}])
        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            task = asyncio.create_task(listen(client, self.store, self.s, self.p, authorize=lambda: allowed['value']))
            await client.ready.wait()
            allowed['value'] = False
            revoked_at = time.monotonic()
            await asyncio.wait_for(task, 4)
        self.assertLess(time.monotonic() - revoked_at, 3)
        self.assertEqual(self.store.session(self.s['id'])['status'], 'interrupted')

    async def test_two_hour_bounds_reach_the_archive(self):
        from paperlife.lifebook.listening_asr import MAX_DURATION_MS
        from paperlife.lifebook.listening_store import normalized_segments
        from paperlife.lifebook.listening_stream import MAX_SECONDS
        from paperlife.lifebook.materials import MaterialError

        self.assertEqual(MAX_SECONDS, 2 * 60 * 60)
        late = {
            'id': 's1',
            'speaker_id': 'v1',
            'text': '虚构',
            'start_ms': MAX_DURATION_MS - 1000,
            'end_ms': MAX_DURATION_MS,
        }
        self.assertEqual(normalized_segments([late])[0]['end_ms'], MAX_DURATION_MS)
        with self.assertRaises(MaterialError):
            normalized_segments([{**late, 'end_ms': MAX_DURATION_MS + 5001}])


if __name__ == '__main__':
    unittest.main()
