"""旁听两端认人：只用合成的虚构信号，不含真实录音。"""

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperlife.lifebook import listening_sides as sides
from paperlife.lifebook.api import build_router
from paperlife.lifebook.store import Store


def tone(seconds, near, seed=1):
    """“旁边”：高频丰富的噪声；“那头”：只有低频的正弦，模拟通话编码与扬声器削掉高频。"""
    count = int(seconds * 16000)
    if near:
        values = np.random.default_rng(seed).normal(0, 3000, count)
    else:
        values = 3000 * np.sin(2 * np.pi * 300 * np.arange(count) / 16000)
    return np.clip(values, -32768, 32767).astype('<i2')


def segment(i, speaker='1', start=None, end=None):
    start = i * 1000 if start is None else start
    return {
        'id': f's{i}',
        'speaker_id': speaker,
        'text': f'虚构{i}',
        'start_ms': start,
        'end_ms': start + 1000 if end is None else end,
    }


class FeatureTests(unittest.TestCase):
    def test_near_voice_keeps_high_frequencies_far_voice_does_not(self):
        self.assertGreater(sides.feature(tone(1, True)), sides.PRIOR_SPLIT + 1)
        self.assertLess(sides.feature(tone(1, False)), sides.PRIOR_SPLIT - 1)
        self.assertIsNone(sides.feature(tone(0.3, True)))
        self.assertIsNone(sides.feature(np.zeros(16000, dtype='<i2')))
        self.assertEqual(sides.feature(tone(1, True).tobytes()), sides.feature(tone(1, True)))

    def test_split_needs_enough_values_and_real_separation(self):
        self.assertIsNone(sides.split([0.0, 1.0]))
        self.assertIsNone(sides.split([0.1, 0.2, 0.15, 0.3, 0.25, 0.2, 0.18]))
        threshold, low, high = sides.split([-0.6, -0.5, -0.55, 0.5, 0.6, 0.55, -0.45, 0.65])
        self.assertTrue(low < threshold < high)
        self.assertAlmostEqual(low, -0.525, places=3)


class ClassifyTests(unittest.TestCase):
    def test_two_sides_with_uncertain_middle_and_group_fill_for_short_sentences(self):
        values = {
            's0': -0.6,
            's1': 0.6,
            's2': -0.55,
            's3': 0.55,
            's4': -0.5,
            's5': 0.5,
            's6': 0.02,
            's7': None,
            's8': None,
        }
        segments = [segment(i, speaker='a' if i % 2 == 0 else 'b') for i in range(9)]
        result = sides.classify(segments, values)
        self.assertEqual(result['mode'], 'two_sides')
        # 落在分界附近的句子不归边，交给人工；算不出特征的短句按同组多数补上。
        self.assertIn('s6', result['uncertain'])
        self.assertNotIn('s6', result['sides'])
        self.assertEqual(result['sides']['s8'], 'far')
        self.assertEqual(result['sides']['s7'], 'near')
        self.assertEqual({k: result['sides'][k] for k in ('s0', 's1')}, {'s0': 'far', 's1': 'near'})

    def test_one_speaking_side_and_unseparated_recordings(self):
        far_only = {f's{i}': -1.2 - i * 0.01 for i in range(8)}
        result = sides.classify([segment(i) for i in range(8)], far_only)
        self.assertEqual(result['mode'], 'one_side')
        self.assertEqual(set(result['sides'].values()), {'far'})
        muddled = {f's{i}': 0.04 + (i % 3) * 0.05 for i in range(8)}
        result = sides.classify([segment(i) for i in range(8)], muddled)
        self.assertEqual(result, {'sides': {}, 'uncertain': [], 'separated': False, 'mode': 'unseparated'})


class LiveTests(unittest.TestCase):
    def test_prior_split_first_then_adapts_and_old_audio_is_dropped(self):
        live = sides.Live()
        live.add(tone(1, True).tobytes() + tone(1, False).tobytes())
        early = live.update([segment(0), segment(1)])
        self.assertEqual(early, {'s0': 'near', 's1': 'far'})
        # 超出保留时长的音频不再算特征，但已算过的句子保留结果。
        live.KEEP_SECONDS = 1
        live.add(tone(1, True).tobytes())
        self.assertEqual(live.start, 32000)
        later = live.update([segment(0), segment(1), segment(2)])
        self.assertEqual(later, {'s0': 'near', 's1': 'far', 's2': 'near'})
        self.assertEqual(live.update([segment(9, start=0, end=500)]), {})


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'life')
        self.user = SimpleNamespace(id=1, role='admin')
        app = FastAPI()
        app.include_router(build_router(self.store, lambda: self.user, lambda uid: True))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.p = self.store.create_profile(1, '虚构主体', '')
        self.store.configure({'api_key': 'synthetic-only'})

    def old_recording(self, pattern):
        from paperlife.lifebook.listening_store import initialize, save_recognition

        s = self.store.create_session(self.p['id'], 1, '虚构旧通话')
        initialize(self.store, s['id'], [])
        with wave.open(str(self.store.audio_path(s['id'])), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            for i, near in enumerate(pattern):
                audio.writeframes(tone(1, near, seed=i).tobytes())
        segs = [segment(i, speaker=str(i % 3)) for i in range(len(pattern))]
        save_recognition(self.store, s['id'], segs, 'complete')
        self.store.finish_session(s['id'], 'ended')
        return s['id']

    def test_old_recordings_get_a_suggestion_once_and_the_api_returns_it(self):
        pattern = [True, False, False, True, False, True, True, False]
        sid = self.old_recording(pattern)
        calls = []
        original = sides.from_audio

        def counting(*args):
            calls.append(1)
            return original(*args)

        with patch.object(sides, 'from_audio', counting):
            first = self.client.get('/life/listening/sessions/' + sid).json()['suggestion']
            second = self.client.get('/life/listening/sessions/' + sid).json()['suggestion']
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first['sides'], {f's{i}': 'near' if near else 'far' for i, near in enumerate(pattern)})

    def test_confirming_sides_saves_attribution_with_readable_group_labels(self):
        sid = self.old_recording([True, False, True, False, True, False])
        data = self.client.get('/life/listening/sessions/' + sid).json()
        people = [
            {'id': 'subject', 'name': '虚构主体', 'relationship': '', 'is_subject': True},
            {'id': 'me', 'name': '我', 'relationship': '', 'is_subject': False},
        ]
        overrides = {k: {'speaker_id': v} for k, v in data['suggestion']['sides'].items()}
        r = self.client.put(
            '/life/listening/sessions/' + sid,
            json={
                'version': 0,
                'participants': people,
                'bindings': {'far': 'subject', 'near': 'me'},
                'overrides': overrides,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()
        self.assertFalse(saved['identity_confirmation_required'])
        labels = {s['id']: s['label'] for s in saved['speakers']}
        self.assertEqual((labels['far'], labels['near']), ('通话那头', '手机旁边'))
        self.assertEqual({s['participant_id'] for s in saved['segments']}, {'subject', 'me'})


if __name__ == '__main__':
    unittest.main()
