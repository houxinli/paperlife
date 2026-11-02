"""旁听自动音量：保存原声和送识别之前，把说话声拉到正常电平。

旁听不用浏览器的回声消除、降噪和自动增益。手机放在外放旁边时，原始电平很低：
2026-11-01 一次真实试录里，说话声中位约 -58 dBFS，两个声音相差约 9 dB，有声时间只有约一半被识别成文字，回听也几乎听不见。
用这套参数离线回放同一录音，说话声中位升到约 -27 dBFS，两个声音相差 3 dB 以内，底噪约 -79 dBFS，没有样本触到限幅。

- 只在“说话”的帧上调整：帧能量比估计的底噪高 12 dB 以上。停顿时增益保持不动，不会把底噪慢慢放大。
- 增益在 1–50 倍之间，目标是说话峰值约 -10 dBFS。峰值包络按说话时间每秒回落一半，换人说话后一两秒内跟上新音量。
- 增益变小很快，避免过载；变大较快但有上限。放大后超过拐点的样本做软限幅，不会硬削波。
- 只调整整体电平，不做降噪或其他处理。
"""

import array
import math
import sys

RATE = 16000
TARGET_PEAK = 0.3 * 32767
MAX_GAIN = 50.0
SPEECH_OVER_FLOOR = 4.0  # 12 dB
MIN_SPEECH_RMS = 3.0
KNEE = 0.9 * 32767
# 说话峰值包络每 RELEASE_SECONDS 秒（只计说话帧）回落一半，增益据此跟上当前说话人的音量。
RELEASE_SECONDS = 1.0
# 增益变大时每 RISE_SECONDS 秒最多翻一倍；变小时每 FALL_SECONDS 秒最多减半。
RISE_SECONDS = 0.25
FALL_SECONDS = 0.05


def _limit(value):
    magnitude = abs(value)
    if magnitude <= KNEE:
        return int(value)
    soft = KNEE + (32767 - KNEE) * math.tanh((magnitude - KNEE) / (32767 - KNEE))
    return int(soft) if value > 0 else -int(soft)


class AutoGain:
    """一次录音一个实例；process 接收并返回 16kHz 单声道 16 位小端 PCM，长度不变。"""

    def __init__(self):
        self.gain = 1.0
        self.envelope = None
        self.floor = None

    def process(self, pcm):
        samples = array.array('h')
        samples.frombytes(pcm)
        if sys.byteorder == 'big':
            samples.byteswap()
        count = len(samples)
        if not count:
            return pcm
        seconds = count / RATE
        rms = math.sqrt(sum(s * s for s in samples) / count)
        peak = max(abs(s) for s in samples)
        # 底噪：遇到更安静的帧立即跟下去，否则每秒最多抬高 3 dB。
        rise = 10 ** (3 / 20 * seconds)
        self.floor = max(rms, 1.0) if self.floor is None else max(1.0, min(rms, self.floor * rise))
        if rms >= MIN_SPEECH_RMS and rms > self.floor * SPEECH_OVER_FLOOR:
            # 说话峰值包络：新峰值立即生效，之后按说话时间逐渐回落。
            release = 0.5 ** (seconds / RELEASE_SECONDS)
            self.envelope = peak if self.envelope is None else max(peak, self.envelope * release)
            desired = min(MAX_GAIN, max(1.0, TARGET_PEAK / max(self.envelope, 1)))
            if desired < self.gain:
                self.gain = max(desired, self.gain * 0.5 ** (seconds / FALL_SECONDS))
            else:
                self.gain = min(desired, self.gain * 2 ** (seconds / RISE_SECONDS))
        if self.gain == 1.0:
            return pcm
        out = array.array('h', (_limit(s * self.gain) for s in samples))
        if sys.byteorder == 'big':
            out.byteswap()
        return out.tobytes()
