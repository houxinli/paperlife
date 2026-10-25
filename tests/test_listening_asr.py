"""旁听 ASR 协议测试；仅虚构文字、假密钥及内存传输。"""
import asyncio
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def response(data, flags=1, sequence=1, compression=1):
    payload = json.dumps(data, ensure_ascii=False).encode()
    if compression:
        payload = gzip.compress(payload)
    prefix = bytes([0x11, 0x90 | flags, 0x10 | compression, 0])
    if flags & 1:
        prefix += struct.pack('>i', sequence)
    return prefix + struct.pack('>I', len(payload)) + payload


def utterance(text='虚构：我捡到一颗蓝石头。', speaker='1', start=0, end=1200, definite=True):
    return {'text': text, 'start_time': start, 'end_time': end, 'definite': definite,
            'additions': {'speaker': speaker}}


class FakeSocket:
    def __init__(self, frames=(), failure=None):
        self.frames = list(frames)
        self.sent = []
        self.closed = False
        self.failure = failure

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        raise self.failure or RuntimeError('fictional-secret private transcript')

    async def close(self):
        self.closed = True


class FakeConnect:
    def __init__(self, socket, failure=None):
        self.socket = socket
        self.failure = failure
        self.options = None

    def __call__(self, endpoint, **kwargs):
        self.endpoint = endpoint
        self.options = kwargs
        return self

    async def __aenter__(self):
        if self.failure:
            raise self.failure
        return self.socket

    async def __aexit__(self, *args):
        await self.socket.close()


class ListeningASRTests(unittest.TestCase):
    def setUp(self):
        # Missing module must fail explicitly before exercising the contract.
        self.assertIsNotNone(importlib.util.find_spec('paperlife.lifebook.listening_asr'),
                             'listening ASR adapter is not implemented')
        from paperlife.lifebook import listening_asr
        self.asr = listening_asr
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SimpleNamespace(root=Path(self.tmp.name), provider=lambda: {'api_key': 'fictional-secret'})

    def test_disabled_default_and_public_config_never_contains_credentials(self):
        self.assertEqual(self.asr.public_config(self.store), {
            'enabled': False, 'configured': True, 'provider': 'volc_seedasr', 'can_configure': False})
        self.assertNotIn('fictional-secret', json.dumps(self.asr.public_config(self.store, True)))
        self.store.provider = lambda: {}
        self.assertFalse(self.asr.configured(self.store))

    def test_config_persists_only_enabled_with_private_permissions(self):
        cfg = self.asr.save_config(self.store, {'enabled': True})
        self.assertTrue(cfg['enabled'])
        path = self.store.root / 'listening-asr.json'
        self.assertEqual(json.loads(path.read_text()), {'enabled': True})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertTrue(self.asr.public_config(self.store, True)['can_configure'])
        for invalid in ({}, {'enabled': 1}, {'enabled': 'true'}, {'enabled': True, 'endpoint': 'https://invalid.example'}, []):
            with self.subTest(data=invalid), self.assertRaises(ValueError):
                self.asr.save_config(self.store, invalid)

    def test_invalid_private_config_and_credentials_fail_closed(self):
        path = self.store.root / 'listening-asr.json'
        for invalid in ('bad-json', '{"enabled":true,"api_key":"fictional-secret"}', '{"enabled":1}', 'x' * 5000):
            path.write_text(invalid)
            with self.subTest(data=invalid[:60]):
                self.assertFalse(self.asr.public_config(self.store)['enabled'])
        for credentials in ({'api_key': 'x\r\ninjected'}, {'api_key': ['fictional-secret']}, {'app_id': 'x'}, {'access_token': 'x'}):
            self.store.provider = lambda credentials=credentials: credentials
            self.assertFalse(self.asr.configured(self.store))

    def run_connection(self, frames, action=None, credentials=None):
        self.asr.save_config(self.store, {'enabled': True})
        if credentials is not None:
            self.store.provider = lambda: credentials
        socket = FakeSocket(frames)
        transport = FakeConnect(socket)
        async def run():
            async with self.asr.connect_asr(self.store) as upstream:
                if action:
                    await action(upstream)
                return [event async for event in upstream.events()]
        with patch.object(self.asr, 'connect', transport):
            result = asyncio.run(run())
        return result, socket, transport

    def test_request_headers_and_gzip_binary_audio_final_frame(self):
        async def audio(upstream):
            await upstream.send_audio(b'\x01\x00' * 3200)
            await upstream.send_audio(b'', final=True)
            with self.assertRaises(ValueError):
                await upstream.send_audio(b'\x00\x00')
        result, socket, transport = self.run_connection([response({'result': {'utterances': []}}, flags=3, sequence=-2)], audio)
        self.assertEqual(transport.endpoint, 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async')
        self.assertEqual(transport.options['additional_headers']['X-Api-Key'], 'fictional-secret')
        self.assertEqual(transport.options['additional_headers']['X-Api-Resource-Id'], 'volc.seedasr.sauc.duration')
        self.assertTrue(transport.options['additional_headers']['X-Api-Request-Id'])
        self.assertIsNone(transport.options['proxy'])
        full, audio_frame, final = socket.sent
        self.assertEqual(full[:4], b'\x11\x10\x11\x00')
        self.assertEqual(struct.unpack('>I', full[4:8])[0], len(full) - 8)
        config = json.loads(gzip.decompress(full[8:]))
        self.assertEqual(config['audio'], {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1})
        self.assertTrue(config['request']['enable_speaker_info'])
        self.assertTrue(config['request']['show_utterances'])
        self.assertTrue(config['request']['enable_nonstream'])
        self.assertEqual(config['request']['result_type'], 'full')
        self.assertFalse(config['request']['enable_ddc'])
        self.assertNotIn('fictional-secret', json.dumps(config))
        self.assertEqual(audio_frame[:4], b'\x11\x20\x01\x00')
        self.assertEqual(gzip.decompress(audio_frame[8:]), b'\x01\x00' * 3200)
        self.assertEqual(final[:4], b'\x11\x22\x01\x00')
        self.assertEqual(gzip.decompress(final[8:]), b'')
        self.assertTrue(result[-1]['final'])
        self.assertTrue(socket.closed)

    def test_legacy_credentials_use_asr_app_key_header(self):
        _, _, transport = self.run_connection([response({}, flags=2)], credentials={'app_id': 'fiction-app', 'access_token': 'fiction-token'})
        headers = transport.options['additional_headers']
        self.assertEqual(headers['X-Api-App-Key'], 'fiction-app')
        self.assertEqual(headers['X-Api-Access-Key'], 'fiction-token')
        self.assertNotIn('X-Api-Key', headers)
        self.assertNotIn('X-Api-App-ID', headers)

    def test_cumulative_revisions_preserve_speaker_and_do_not_end_at_definite(self):
        first = utterance('虚构：蓝石头', '1', definite=False)
        fixed = utterance('虚构：我捡到蓝石头。', '1')
        second = utterance('虚构：放进盒子。', None, 1400, 2200)
        third = utterance('虚构：纸盒。', None, 1400, 2300)
        events, _, _ = self.run_connection([
            response({'result': {'utterances': [first]}}, sequence=1),
            response({'result': {'utterances': [fixed, second]}}, sequence=2),
            response({'result': {'utterances': [fixed, third]}}, flags=3, sequence=-3),
        ])
        self.assertEqual([event['final'] for event in events], [False, False, True])
        self.assertEqual(events[-1]['segments'], [
            {'id': 'segment-1', 'speaker_id': '1', 'text': '虚构：我捡到蓝石头。', 'start_ms': 0, 'end_ms': 1200},
            {'id': 'segment-2', 'speaker_id': 'unknown', 'text': '虚构：纸盒。', 'start_ms': 1400, 'end_ms': 2300}])
        self.assertEqual(events[0]['segments'][0]['id'], events[-1]['segments'][0]['id'])

    def test_empty_final_ack_keeps_previous_cumulative_snapshot(self):
        events, _, _ = self.run_connection([response({'result': {'utterances': [utterance()]} }), response({}, flags=2)])
        self.assertEqual(events[-1]['segments'], events[0]['segments'])
        self.assertTrue(events[-1]['final'])

    def test_direct_speaker_id_and_string_additions_and_zero_speaker(self):
        items = [utterance(speaker=0), utterance(start=1500, end=1600), utterance(start=1700, end=1800)]
        items[1]['additions'] = json.dumps({'speaker_id': 'speaker-b'})
        items[2]['speaker_id'] = 'speaker-c'
        events, _, _ = self.run_connection([response({'result': [{'utterances': items}]}, flags=2, compression=0)])
        self.assertEqual([s['speaker_id'] for s in events[0]['segments']], ['0', 'speaker-b', 'speaker-c'])

    def test_negative_sequence_is_terminal_even_without_last_flag(self):
        events, _, _ = self.run_connection([response({'result': {'utterances': [utterance()]}}, sequence=-1)])
        self.assertTrue(events[-1]['final'])

    def test_clean_close_without_terminal_is_incomplete(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket([response({'result': {'utterances': [utterance()]}})])
        async def run():
            events = []
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    with self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_incomplete$'):
                        async for event in upstream.events():
                            events.append(event)
            return events
        events = asyncio.run(run())
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]['final'])
        self.assertTrue(socket.closed)

    def test_rejects_malformed_truncated_oversized_and_gzip_bomb(self):
        valid = response({'result': {'utterances': [utterance()]}})
        malformed = [b'', b'\x11', valid[:-1], valid + b'x', b'\x11\x91\x11\x00' + b'\x00' * 7,
                     bytes([0x21]) + valid[1:], valid[:2] + b'\x12' + valid[3:],
                     response({'result': {'utterances': 'bad'}}, flags=2),
                     response({'result': {'utterances': [{'text': 'x', 'start_time': 5, 'end_time': 1}]}}, flags=2),
                     response({'result': {'utterances': [{'text': 'x', 'start_time': True, 'end_time': 5}]}}, flags=2),
                     response({'result': {'utterances': [{'text': 'x', 'start_time': 0, 'end_time': 1, 'speaker_id': '<invalid>'}]}}, flags=2),
                     b'x' * (self.asr.MAX_RESPONSE_BYTES + 1)]
        bomb = gzip.compress(b'x' * (self.asr.MAX_DECOMPRESSED_BYTES + 1))
        malformed.append(b'\x11\x92\x11\x00' + struct.pack('>I', len(bomb)) + bomb)
        for frame in malformed:
            with self.subTest(frame=frame[:24]), self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_protocol$'):
                self.run_connection([frame])

    def test_provider_and_connection_errors_are_sanitized_and_close(self):
        secret = b'fictional-secret private transcript'
        provider_error = b'\x11\xf0\x10\x00' + struct.pack('>II', 45000001, len(secret)) + secret
        for frame in (provider_error, response({'code': 45000001, 'message': secret.decode()}, flags=2)):
            with self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_provider_error$'):
                self.run_connection([frame])
        self.asr.save_config(self.store, {'enabled': True})
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(FakeSocket(), RuntimeError(secret.decode()))):
                async with self.asr.connect_asr(self.store):
                    self.fail('connect should fail')
        with self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_unavailable$'):
            asyncio.run(run())

    def test_disabled_and_missing_credentials_do_not_open_transport(self):
        async def run():
            async with self.asr.connect_asr(self.store):
                self.fail('disabled adapter should not connect')
        with patch.object(self.asr, 'connect') as transport:
            with self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_disabled$'):
                asyncio.run(run())
            transport.assert_not_called()
            self.asr.save_config(self.store, {'enabled': True})
            self.store.provider = lambda: {}
            with self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_not_configured$'):
                asyncio.run(run())
            transport.assert_not_called()

    def test_terminal_result_blocks_further_audio_and_snapshot_mutation(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket([response({'result': {'utterances': [utterance()]}}), response({}, flags=2)])
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    iterator = upstream.events()
                    interim = await anext(iterator)
                    interim['segments'][0]['text'] = 'caller changed display'
                    final = await anext(iterator)
                    self.assertEqual(final['segments'][0]['text'], '虚构：我捡到一颗蓝石头。')
                    with self.assertRaises(ValueError):
                        await upstream.send_audio(b'\x00\x00')
        asyncio.run(run())

    def test_chunked_final_audio_and_consumer_exception_close_transport(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket()
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    await upstream.send_audio(b'\x00\x00' * 7000, final=True)
                    raise LookupError('caller failure')
        with self.assertRaisesRegex(LookupError, '^caller failure$'):
            asyncio.run(run())
        self.assertTrue(socket.closed)
        frames = socket.sent[1:]
        self.assertEqual([len(gzip.decompress(frame[8:])) for frame in frames], [6400, 6400, 1200])
        self.assertEqual([frame[1] for frame in frames], [0x20, 0x20, 0x22])

    def test_20ms_capture_is_buffered_to_200ms_and_final_flushes_remainder(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket()
        chunks = [bytes([index, 0]) * 320 for index in range(11)]
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    for chunk in chunks[:9]:
                        await upstream.send_audio(chunk)
                    self.assertEqual(len(socket.sent), 1, 'sub-200ms audio must remain buffered')
                    await upstream.send_audio(chunks[9])
                    self.assertEqual(len(socket.sent), 2)
                    await upstream.send_audio(chunks[10])
                    self.assertEqual(len(socket.sent), 2)
                    await upstream.send_audio(b'\x63\x00' * 160, final=True)
                    with self.assertRaises(ValueError):
                        await upstream.send_audio(b'', final=True)
        asyncio.run(run())
        frames = socket.sent[1:]
        self.assertEqual([len(gzip.decompress(frame[8:])) for frame in frames], [6400, 960])
        self.assertEqual([frame[1] for frame in frames], [0x20, 0x22])
        self.assertEqual(gzip.decompress(frames[0][8:]), b''.join(chunks[:10]))
        self.assertEqual(gzip.decompress(frames[1][8:]), chunks[10] + b'\x63\x00' * 160)

    def test_buffered_bytes_count_toward_limit_and_close_does_not_flush(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket()
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    with patch.object(self.asr, 'MAX_AUDIO_BYTES', 640):
                        await upstream.send_audio(b'\x00\x00' * 320)
                        with self.assertRaises(ValueError):
                            await upstream.send_audio(b'\x00\x00')
        asyncio.run(run())
        self.assertEqual(len(socket.sent), 1, 'close must not submit buffered audio or fabricate final')
        self.assertTrue(socket.closed)

    def test_speaker_ids_match_core_grammar_without_lossy_renaming(self):
        valid_ids = ['_speaker-1', '-1', 'speaker_' + 'x' * 72]
        events, _, _ = self.run_connection([response({'result': {'utterances': [
            {**utterance(), 'speaker_id': speaker} for speaker in valid_ids]}}, flags=2)])
        self.assertEqual([item['speaker_id'] for item in events[0]['segments']], valid_ids)
        for speaker in ('speaker.1', 'speaker:1', 'a' * 81):
            with self.subTest(speaker=speaker), self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_protocol$'):
                self.run_connection([response({'result': {'utterances': [{**utterance(), 'speaker_id': speaker}]}}, flags=2)])

    def test_real_stream_interim_missing_start_uses_provider_word_time(self):
        # Sanitized synthetic probe shape: first streaming utterance omits start_time.
        interim = {'audio_info': {'duration': 600}, 'result': {'text': '这', 'utterances': [
            {'additions': {'fixed_prefix_result': '', 'source': 'stream'}, 'definite': False,
             'end_time': 600, 'text': '这', 'words': [{'start_time': 340, 'end_time': 420, 'text': '这'}]}]}}
        final = {'result': {'utterances': [utterance('这是虚构的蓝石头。', '1', 340, 1200)]}}
        events, _, _ = self.run_connection([response(interim), response(final, flags=2)])
        self.assertEqual(events[0], {'segments': [
            {'id': 'segment-1', 'speaker_id': 'unknown', 'text': '这', 'start_ms': 340, 'end_ms': 600}], 'final': False})
        self.assertEqual(events[-1]['segments'][0]['text'], '这是虚构的蓝石头。')
        self.assertTrue(events[-1]['final'])
        self.assertEqual(len(events[-1]['segments']), 1)

    def test_missing_time_bounds_derive_from_valid_words_only(self):
        items = [
            {'text': '虚构甲', 'end_time': 900, 'words': [{'start_time': 100, 'end_time': 300}, {'start_time': 350, 'end_time': 850}]},
            {'text': '虚构乙', 'start_time': 1000, 'words': [{'start_time': 1020, 'end_time': 1100}, {'start_time': 1120, 'end_time': 1200}]},
            {'text': '虚构丙', 'words': [{'start_time': 1500, 'end_time': 1700}, {'start_time': 1400, 'end_time': 1600}]},
        ]
        events, _, _ = self.run_connection([response({'result': {'utterances': items}}, flags=2)])
        self.assertEqual([(s['start_ms'], s['end_ms']) for s in events[0]['segments']],
                         [(100, 900), (1000, 1200), (1400, 1700)])
        invalid = [
            {'text': '无时间', 'words': []},
            {'text': '无时间'},
            {'text': '无时间', 'words': [{'start_time': True, 'end_time': 100}]},
            {'text': '无时间', 'words': [{'start_time': 100, 'end_time': 50}]},
            {'text': '无时间', 'words': [{'start_time': -1, 'end_time': 100}]},
            {'text': '无时间', 'words': [{'start_time': 0, 'end_time': 1200001}]},
            {'text': '无时间', 'words': [{'end_time': 100}]},
            {'text': '无时间', 'words': 'invalid'},
            {'text': '无时间', 'start_time': True, 'words': [{'start_time': 0, 'end_time': 100}]},
            {'text': '无时间', 'end_time': 50, 'words': [{'start_time': 100, 'end_time': 200}]},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaisesRegex(self.asr.ASRError, '^listening_asr_protocol$'):
                self.run_connection([response({'audio_info': {'duration': 1000}, 'result': {'utterances': [item]}}, flags=2)])

    def test_audio_bounds_alignment_and_session_duration(self):
        self.asr.save_config(self.store, {'enabled': True})
        socket = FakeSocket()
        async def run():
            with patch.object(self.asr, 'connect', FakeConnect(socket)):
                async with self.asr.connect_asr(self.store) as upstream:
                    for audio in (b'x', 'not-bytes', b'\x00' * (self.asr.MAX_AUDIO_FRAME_BYTES + 2)):
                        with self.assertRaises(ValueError):
                            await upstream.send_audio(audio)
                    with patch.object(self.asr, 'MAX_AUDIO_BYTES', 4):
                        await upstream.send_audio(b'\x00' * 4)
                        with self.assertRaises(ValueError):
                            await upstream.send_audio(b'\x00' * 2)
                        await upstream.send_audio(b'', final=True)
        asyncio.run(run())
        self.assertTrue(socket.closed)


if __name__ == '__main__':
    unittest.main()
