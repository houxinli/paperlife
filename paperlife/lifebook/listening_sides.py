"""旁听两端认人：把每句话归到“通话那头”或“手机旁边”。

家人用另一台设备视频并外放，旁听设备放在旁边。通话那头的声音经过通话编码和扬声器，4 kHz 以上几乎没有能量；
旁边的人直接对着麦克风说，高频完整。2026-11-07 一次真实通话（只看电平特征，不读内容）里，识别服务把两个人
切成了 6 个声音组；按 4 kHz 以上的能量占比，那头约 0.3–0.8%，旁边约 2–4%，干净地分成两边。

- 每句（至少 0.6 秒、不是静音）算一个特征：log10(4–8 kHz 能量占 0.1–8 kHz 能量的百分比)。
- 在这次录音里分成两类：排序后取两类组内方差之和最小的切点，分界取两类中心的中点。两类中心相差不到 0.5（约 3 倍）就算分不开。
- 分不开时，如果所有句子都明显落在先验分界（约 1.1%）的同一侧，就整段归到那一边（只有一方说话）；否则不给建议，不乱猜。
- 用识别服务的声音组做多数平滑：同组算得出特征的句子里至少 3 句、70% 以上在同一边时，这组里太短、算不出特征的句子也归到这一边。
- 拿不准的不猜：离分界不到 0.2（约 1.6 倍）的句子不归边，确认时提醒人工看一眼。同一次真实通话里，
  长句常把一问一答切成一句、两个人的声音混在一起，这类句子特征落在分界附近；留出余量后，归了边的句子与识别服务
  声音组的多数一致率从约 81% 升到约 87%，剩下的不一致里不少看起来是声音组本身混了人。
- 这只是建议，人工确认后才写进归属。它不是声纹，不保存任何人的声音特征。
"""

import json
import wave
from collections import defaultdict

import numpy as np

NEAR, FAR = 'near', 'far'
RATE = 16000
MIN_SECONDS = 0.6
MIN_RMS = 10 ** (-60 / 20)
MIN_SEPARATION = 0.5
PRIOR_SPLIT = float(np.log10(1.1))
ONE_SIDE_MARGIN = 0.25
MIN_CLASSIFIED = 6
GROUP_MIN = 3
GROUP_SHARE = 0.7
CONFIDENT_MARGIN = 0.2


def feature(pcm):
    """一句话的高频占比特征；太短或太安静返回 None。pcm 为 16 位单声道样本数组或字节。"""
    samples = np.frombuffer(pcm, dtype='<i2') if isinstance(pcm, (bytes, bytearray, memoryview)) else pcm
    if len(samples) < MIN_SECONDS * RATE:
        return None
    x = samples.astype(np.float64) / 32768
    if float(np.sqrt(np.mean(x * x))) < MIN_RMS:
        return None
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    total = float(spectrum[freqs >= 100].sum())
    if total <= 0:
        return None
    high = float(spectrum[freqs >= 4000].sum())
    return float(np.log10(max(high / total * 100, 1e-4)))


def split(values):
    """一维两类的分界 (threshold, low_center, high_center)；样本太少或两类分不开时返回 None。"""
    v = np.sort(np.asarray(values, dtype=np.float64))
    n = len(v)
    if n < MIN_CLASSIFIED:
        return None
    smallest = max(2, int(np.ceil(n * 0.05)))
    prefix, prefix_sq = np.cumsum(v), np.cumsum(v * v)
    best = None
    for i in range(smallest, n - smallest + 1):
        low_n, high_n = i, n - i
        low_sum, high_sum = prefix[i - 1], prefix[-1] - prefix[i - 1]
        low_sq, high_sq = prefix_sq[i - 1], prefix_sq[-1] - prefix_sq[i - 1]
        cost = (low_sq - low_sum * low_sum / low_n) + (high_sq - high_sum * high_sum / high_n)
        if best is None or cost < best[0]:
            best = (cost, i, low_sum / low_n, high_sum / high_n)
    if best is None:
        return None
    _, i, low, high = best
    if high - low < MIN_SEPARATION:
        return None
    # 分界取两类中心的中点：比取交界处相邻两值的中点稳，不被边缘上的个别句子带偏。
    return float((low + high) / 2), float(low), float(high)


def classify(segments, features):
    """features: {segment_id: 特征或 None}。

    返回 {'sides': {segment_id: near|far}, 'uncertain': [segment_id], 'separated': bool, 'mode': ...}。
    """
    known = {ident: value for ident, value in features.items() if value is not None}
    result = split(list(known.values()))
    if result:
        threshold, low, high = result
        mode = 'two_sides'
    elif known and (
        max(known.values()) < PRIOR_SPLIT - ONE_SIDE_MARGIN or min(known.values()) > PRIOR_SPLIT + ONE_SIDE_MARGIN
    ):
        threshold, low, high = PRIOR_SPLIT, None, None
        mode = 'one_side'
    else:
        return {'sides': {}, 'uncertain': [], 'separated': False, 'mode': 'unseparated'}
    margin = CONFIDENT_MARGIN if mode == 'two_sides' else 0
    sides = {
        ident: NEAR if value > threshold else FAR for ident, value in known.items() if abs(value - threshold) >= margin
    }
    uncertain = [ident for ident in known if ident not in sides]
    groups = defaultdict(list)
    for segment in segments:
        if segment['speaker_id'] != 'unknown':
            groups[segment['speaker_id']].append(segment['id'])
    for ids in groups.values():
        labelled = [sides[i] for i in ids if i in sides]
        if len(labelled) < GROUP_MIN:
            continue
        top = max((NEAR, FAR), key=labelled.count)
        if labelled.count(top) / len(labelled) >= GROUP_SHARE:
            # 只补太短、算不出特征的句子；拿不准的长句可能混了两个人，不按声音组硬归。
            for ident in ids:
                if ident not in features or features[ident] is None:
                    sides.setdefault(ident, top)
    return {
        'sides': sides,
        'uncertain': uncertain,
        'separated': True,
        'mode': mode,
        'threshold': round(threshold, 3),
        'centers': [round(low, 3), round(high, 3)] if low is not None else None,
    }


def from_audio(path, segments):
    """读整段原声，按句算特征并归边。"""
    with wave.open(str(path)) as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
    features = {
        s['id']: feature(samples[int(s['start_ms'] * RATE / 1000) : int(s['end_ms'] * RATE / 1000)]) for s in segments
    }
    return classify(segments, features)


class Live:
    """录音中的实时归边：只保留最近两分钟的音频；分界随录音自适应，最初用先验分界。"""

    KEEP_SECONDS = 120

    def __init__(self):
        self.recent = bytearray()
        self.start = 0
        self.features = {}

    def add(self, pcm):
        self.recent.extend(pcm)
        extra = len(self.recent) - self.KEEP_SECONDS * RATE * 2
        if extra > 0:
            del self.recent[:extra]
            self.start += extra // 2

    def _audio(self, segment):
        first = int(segment['start_ms'] * RATE / 1000) - self.start
        last = int(segment['end_ms'] * RATE / 1000) - self.start
        if first < 0 or last * 2 > len(self.recent) or last <= first:
            return None
        return bytes(self.recent[first * 2 : last * 2])

    def update(self, segments):
        """为全部句子归边（特征按句子边界缓存，边界变了就重算）；拿不准的标成 near? / far?。"""
        for segment in segments:
            key = (segment['start_ms'], segment['end_ms'])
            if key in self.features:
                continue
            audio = self._audio(segment)
            if audio is not None:
                self.features[key] = feature(audio)
        values = {s['id']: self.features.get((s['start_ms'], s['end_ms'])) for s in segments}
        result = split([v for v in values.values() if v is not None])
        threshold = result[0] if result else PRIOR_SPLIT
        sides = {}
        for ident, value in values.items():
            if value is None:
                continue
            side = NEAR if value > threshold else FAR
            sides[ident] = side if abs(value - threshold) >= CONFIDENT_MARGIN else side + '?'
        return sides


def stored(store, sid):
    with store.db() as db:
        row = db.execute('SELECT data FROM listening_sides WHERE session=?', (sid,)).fetchone()
    return json.loads(row['data']) if row else None


def save(store, sid, suggestion):
    with store.db() as db:
        db.execute(
            'INSERT INTO listening_sides(session,data) VALUES(?,?) ON CONFLICT(session) DO UPDATE SET data=excluded.data',
            (sid, json.dumps(suggestion)),
        )


def refresh(store, sid, segments):
    """定稿后按整段原声重算并保存。"""
    path = store.audio_path(sid)
    result = (
        from_audio(path, segments)
        if segments and path.exists()
        else {'sides': {}, 'uncertain': [], 'separated': False, 'mode': 'unseparated'}
    )
    save(store, sid, result)
    return result


def suggestion(store, sid, segments):
    """定稿后的两端建议；旧录音第一次查看时补算并保存。原声缺失时不给建议。"""
    existing = stored(store, sid)
    if existing is not None:
        return existing
    path = store.audio_path(sid)
    if not segments or not path.exists():
        return {'sides': {}, 'uncertain': [], 'separated': False, 'mode': 'unseparated'}
    result = from_audio(path, segments)
    save(store, sid, result)
    return result
