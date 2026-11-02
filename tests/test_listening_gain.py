"""旁听自动音量：只用合成的虚构信号，不含真实录音。"""

import math
import struct
import unittest

from paperlife.lifebook.listening_gain import MAX_GAIN, TARGET_PEAK, AutoGain


def frame(amplitude, count=320, start=0, freq=300):
    # 20ms：正弦“说话声”加一点确定性的底噪。
    values = [
        int(amplitude * math.sin(2 * math.pi * freq * (start + i) / 16000)) + ((start + i) * 7919 % 5) - 2
        for i in range(count)
    ]
    return struct.pack(f'<{count}h', *values)


def samples(pcm):
    return struct.unpack(f'<{len(pcm) // 2}h', pcm)


def run(gain, pattern, seconds):
    """pattern(second) 给出该秒的说话振幅；返回每帧的 (输入, 输出)。"""
    frames = []
    for index in range(int(seconds * 50)):
        pcm = frame(pattern(index / 50), start=index * 320)
        frames.append((pcm, gain.process(pcm)))
    return frames


def db(value):
    return 20 * math.log10(value)


def talk(t):
    # 说 1 秒、停 0.5 秒；真实说话的词与词之间也有更安静的帧，底噪靠这些帧来估计。
    return 330 if t % 1.5 < 1 else 0


class AutoGainTests(unittest.TestCase):
    def test_quiet_speech_is_raised_to_target_within_seconds(self):
        # 试录实测：说话声中位约 -60 dBFS，峰值约 -40 dBFS（≈330）。说 1 秒、停 0.5 秒交替。
        gain = AutoGain()
        frames = run(gain, talk, 8)
        late = [max(map(abs, samples(out))) for pcm, out in frames[-75:] if max(map(abs, samples(pcm))) > 200]
        self.assertTrue(late)
        self.assertLess(abs(db(max(late)) - db(TARGET_PEAK)), 3)
        self.assertLessEqual(gain.gain, MAX_GAIN)
        self.assertGreater(gain.gain, 20)

    def test_silence_steady_noise_and_normal_speech_pass_through_unchanged(self):
        for amplitude in (0, 16000):
            with self.subTest(amplitude=amplitude):
                gain = AutoGain()
                frames = run(gain, lambda t: amplitude if t % 1.5 < 1 else 0, 6)
                self.assertTrue(all(pcm == out for pcm, out in frames))
                self.assertEqual(gain.gain, 1.0)

    def test_pauses_hold_the_gain_instead_of_amplifying_the_noise_floor(self):
        gain = AutoGain()
        run(gain, talk, 5)
        held = gain.gain
        self.assertGreater(held, 20)
        run(gain, lambda t: 0, 10)
        self.assertEqual(gain.gain, held)

    def test_sudden_loud_sound_is_limited_and_gain_falls_back_quickly(self):
        gain = AutoGain()
        run(gain, talk, 5)
        self.assertGreater(gain.gain, 20)
        loud = run(gain, lambda t: 20000, 0.3)
        for _, out in loud:
            self.assertTrue(all(-32768 <= s <= 32767 for s in samples(out)))
        self.assertEqual(gain.gain, 1.0)
        # 放大后远超满幅的样本被限幅而不是回绕成反相噪声。
        first_in, first_out = samples(loud[0][0]), samples(loud[0][1])
        self.assertTrue(all((a >= 0) == (b >= 0) for a, b in zip(first_in, first_out) if abs(a) > 100))

    def test_length_is_preserved_and_empty_input_is_untouched(self):
        gain = AutoGain()
        run(gain, talk, 3)
        self.assertGreater(gain.gain, 1)
        pcm = frame(330, count=3200)
        self.assertEqual(len(gain.process(pcm)), len(pcm))
        self.assertEqual(gain.process(b''), b'')


if __name__ == '__main__':
    unittest.main()
