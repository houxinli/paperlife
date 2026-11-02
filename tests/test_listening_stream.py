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

    async def test_live_captions_send_changed_tail_that_rebuilds_the_full_transcript(self):
        from paperlife.lifebook.listening_store import detail

        a, b, c = fictional_segments(3)
        revised = {**b, 'text': '虚构第2句，修订后更完整。'}
        merged = {**b, 'text': '虚构第2句和第3句合成一句。'}
        many = fictional_segments(200)
        partials = [[a], [a, b], [a, revised], [a, revised, c], [a, merged], many]
        client, _ = await self.run_stream(provider=ScriptedProvider(partials, many))
        live = [e for e in client.sent if e['type'] == 'listening_transcript']
        self.assertEqual(
            [(e['from'], len(e['segments']), e['total']) for e in live[:5]],
            [
                (0, 1, 1),  # 第一句
                (1, 1, 2),  # 新增一句只发这一句
                (1, 1, 2),  # 临时修订只重发被改的那一句
                (2, 1, 3),
                (1, 1, 2),  # 两句合并：从变化处重发，并用 total 截掉多余的旧句
            ],
        )
        # 网页按 from/total 拼接后得到与档案一致的全文，不再只显示最近几句。
        rebuilt = []
        for event in live:
            rebuilt[event['from'] :] = event['segments']
            del rebuilt[event['total'] :]
        self.assertTrue(live[-1]['final'])
        self.assertEqual(live[-1]['segments'], [])
        self.assertEqual(rebuilt, detail(self.store, self.s['id'])['original_segments'])
        self.assertEqual(len(rebuilt), 200)

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


class QuietAudioTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = StreamTests.asyncSetUp
    asyncTearDown = StreamTests.asyncTearDown

    async def test_quiet_speech_is_raised_before_saving_and_recognition(self):
        import math
        import struct
        from paperlife.lifebook.listening_stream import listen

        def frame(index):
            # 虚构合成信号：说 1 秒、停 0.5 秒，说话峰值约 -40 dBFS，与真实外放旁录的电平相当。
            amplitude = 330 if (index / 50) % 1.5 < 1 else 0
            values = [int(amplitude * math.sin(2 * math.pi * 300 * (index * 320 + i) / 16000)) for i in range(320)]
            return struct.pack('<320h', *values)

        sent = [frame(i) for i in range(200)]
        provider = Provider()

        @asynccontextmanager
        async def connect(_):
            yield provider

        client = Client([{'type': 'websocket.receive', 'bytes': pcm} for pcm in sent] + [STOP])
        with patch('paperlife.lifebook.listening_stream.connect_asr', connect):
            await asyncio.wait_for(listen(client, self.store, self.s, self.p, authorize=lambda: True), 4)
        with wave.open(str(self.store.audio_path(self.s['id']))) as f:
            saved = f.readframes(f.getnframes())
        recognized = b''.join(chunk for chunk in provider.audio if chunk)
        self.assertEqual(len(saved), len(b''.join(sent)))
        # 保存的原声与送去识别的是同一份调整后的音频，说话声明显变大。
        self.assertEqual(saved, recognized)
        peak = lambda pcm: max(abs(v) for v in struct.unpack(f'<{len(pcm) // 2}h', pcm))
        self.assertGreater(peak(saved[-32000:]), 10 * peak(b''.join(sent)[-32000:]))


class WaitingClient(Client):
    """脚本消息发完后，等收到指定事件再发“结束”，让后台的实时笔记有机会运行。"""

    def __init__(self, messages, until):
        super().__init__(messages)
        self.until = until
        self.seen = asyncio.Event()

    async def send_json(self, event):
        await super().send_json(event)
        if event['type'] == self.until:
            self.seen.set()

    async def receive(self):
        await self.ready.wait()
        if self.messages:
            return self.messages.pop(0)
        await asyncio.wait_for(self.seen.wait(), 3)
        return STOP


PEOPLE = [
    {'id': 'subject', 'name': '虚构长辈', 'relationship': '', 'is_subject': True},
    {'id': 'me', 'name': '虚构家人', 'relationship': '孙辈', 'is_subject': False},
]
TWO_VOICES = [
    {'id': 's1', 'speaker_id': 'voice1', 'text': '虚构：那年我们住在河边。', 'start_ms': 0, 'end_ms': 900},
    {'id': 's2', 'speaker_id': 'voice2', 'text': '虚构：后来呢？', 'start_ms': 1000, 'end_ms': 1500},
]


class LiveAssistTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = StreamTests.asyncSetUp
    asyncTearDown = StreamTests.asyncTearDown
    new_session = StreamTests.new_session

    async def record(self, controls, client=None, notes=None, partials=(TWO_VOICES,)):
        from paperlife.lifebook.listening_stream import listen

        provider = ScriptedProvider(list(partials), TWO_VOICES)

        @asynccontextmanager
        async def connect(_):
            yield provider

        messages = [{'type': 'websocket.receive', 'bytes': b'\0\1' * 320}]
        messages += [{'type': 'websocket.receive', 'text': json.dumps(c, ensure_ascii=False)} for c in controls]
        client = client(messages) if client else Client(messages + [STOP])
        patches = [patch('paperlife.lifebook.listening_stream.connect_asr', connect)]
        if notes:
            patches += [
                patch('paperlife.lifebook.listening_stream.live_notes', notes),
                patch('paperlife.lifebook.listening_stream.NOTES_SECONDS', 0.01),
                patch('paperlife.lifebook.listening_stream.NOTES_MIN_CHARS', 1),
            ]
        for item in patches:
            item.start()
        try:
            await asyncio.wait_for(listen(client, self.store, self.s, self.p, authorize=lambda: True), 5)
        finally:
            for item in patches:
                item.stop()
        return client

    async def test_live_labels_become_the_first_attribution_version(self):
        from paperlife.lifebook.listening_store import detail

        labels = {
            'type': 'labels',
            'participants': PEOPLE,
            'labels': {'voice1': 'subject', 'voice2': 'me', 'voice9': 'me'},
        }
        client = await self.record([labels])
        saved = [e for e in client.sent if e['type'] == 'saved' and 'recognition_status' in e]
        self.assertTrue(saved[-1]['labels_saved'])
        record = detail(self.store, self.s['id'])
        self.assertEqual(record['session']['version'], 1)
        # 最终识别里没有的声音组不保存；主体已由家人标注，素材可以进入整理。
        self.assertEqual(
            {s['id']: s['participant_id'] for s in record['speakers']}, {'voice1': 'subject', 'voice2': 'me'}
        )
        self.assertFalse(record['identity_confirmation_required'])
        # 原始识别不变，归属是追加的人工版本。
        self.assertEqual([s['text'] for s in record['original_segments']], [s['text'] for s in TWO_VOICES])

    async def test_invalid_labels_and_guest_labels_are_ignored_without_stopping(self):
        from paperlife.lifebook.listening_store import detail, initialize

        bad = {'type': 'labels', 'participants': PEOPLE, 'labels': {'voice1': 'nobody'}}
        client = await self.record([bad])
        self.assertEqual(detail(self.store, self.s['id'])['session']['version'], 0)
        self.assertEqual(client.sent[-1]['status'], 'ended')
        self.assertFalse(client.sent[-2]['labels_saved'])

        self.s = self.store.create_session(self.p['id'], -7, '虚构长辈自己录')
        initialize(self.store, self.s['id'], [])
        self.s = self.store.session(self.s['id'])
        await self.record([{'type': 'labels', 'participants': PEOPLE, 'labels': {'voice1': 'subject'}}])
        self.assertEqual(detail(self.store, self.s['id'])['session']['version'], 0)

    async def test_notes_use_live_names_skip_known_points_and_stay_off_the_archive(self):
        calls = []

        async def notes(store, subject, segments, names, known, timeout):
            calls.append(
                {'subject': subject, 'names': dict(names), 'known': list(known), 'texts': [s['text'] for s in segments]}
            )
            return {
                'points': [{'text': '虚构长辈说那年住在河边', 'speaker': '虚构长辈'}],
                'questions': [{'text': '河边的房子是什么样的？'}],
            }

        labels = {'type': 'labels', 'participants': PEOPLE, 'labels': {'voice1': 'subject'}}
        client = await self.record(
            [labels, {'type': 'notes', 'enabled': True}],
            client=lambda messages: WaitingClient(messages, 'listening_notes'),
            notes=notes,
        )
        events = [e for e in client.sent if e['type'] == 'listening_notes']
        self.assertEqual(events[0]['points'][0]['text'], '虚构长辈说那年住在河边')
        self.assertEqual(events[0]['questions'][0]['text'], '河边的房子是什么样的？')
        self.assertEqual(calls[0]['subject'], '虚构主体')
        self.assertEqual(calls[0]['names'], {'voice1': '虚构长辈'})
        self.assertIn('虚构：后来呢？', calls[0]['texts'])
        self.assertEqual(client.sent[-1]['status'], 'ended')
        # 笔记不写进档案：识别原文和转写里都没有笔记内容。
        self.assertNotIn('河边的房子', json.dumps(self.store.turns(self.s['id']), ensure_ascii=False))

    async def test_notes_failures_never_stop_recording_and_unconfigured_turns_them_off(self):
        calls = []

        async def unconfigured(*args):
            calls.append(1)
            raise ValueError('provider_unconfigured')

        client = await self.record(
            [{'type': 'notes', 'enabled': True}],
            client=lambda messages: WaitingClient(messages, 'listening_notes'),
            notes=unconfigured,
        )
        errors = [e['error'] for e in client.sent if e['type'] == 'listening_notes']
        self.assertEqual(errors, ['文本模型尚未配置，AI 笔记已关闭。'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(client.sent[-1]['status'], 'ended')
        self.assertEqual(client.sent[-1]['recognition_status'], 'complete')

    async def test_notes_are_off_unless_the_page_turns_them_on(self):
        calls = []

        async def notes(*args):
            calls.append(1)
            return {'points': [], 'questions': []}

        await self.record([], notes=notes)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
