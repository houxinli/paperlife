"""旁听原始识别与追加归属版本；名单不会推断声音身份。"""

import json
import re
from contextlib import nullcontext
from .materials import MaterialError, ConflictError

IDENT = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def validate_participants(participants):
    if not isinstance(participants, list) or len(participants) > 30:
        raise MaterialError('参与者最多30位')
    seen = set()
    result = []
    for p in participants:
        if (
            not isinstance(p, dict)
            or not isinstance(p.get('id'), str)
            or not IDENT.fullmatch(p['id'])
            or p['id'] in seen
        ):
            raise MaterialError('参与者编号无效或重复')
        if not isinstance(p.get('name'), str) or not 1 <= len(p['name'].strip()) <= 80:
            raise MaterialError('参与者姓名应为1至80字')
        if (
            not isinstance(p.get('relationship', ''), str)
            or len(p.get('relationship', '')) > 100
            or not isinstance(p.get('is_subject', False), bool)
        ):
            raise MaterialError('参与者关系或主体标记无效')
        seen.add(p['id'])
        result.append(
            {
                'id': p['id'],
                'name': p['name'].strip(),
                'relationship': p.get('relationship', ''),
                'is_subject': p.get('is_subject', False),
            }
        )
    if sum(p['is_subject'] for p in result) > 1:
        raise MaterialError('只能选择一位档案主体')
    return result


def initialize(store, sid, participants):
    participants = validate_participants(participants)
    with store.db() as db:
        db.execute("UPDATE sessions SET mode='listening' WHERE id=?", (sid,))
        db.execute('INSERT INTO listening_originals(session) VALUES(?)', (sid,))
        db.execute(
            'INSERT INTO listening_annotations(session,version,participants,bindings,overrides) VALUES(?,0,?,\'{}\',\'{}\')',
            (sid, json.dumps(participants, ensure_ascii=False)),
        )


def normalized_segments(segments):
    if not isinstance(segments, list) or len(segments) > 20000:
        raise MaterialError('识别分句无效或过多')
    result = []
    seen = set()
    for i, s in enumerate(segments):
        if not isinstance(s, dict):
            raise MaterialError('识别分句无效')
        ident = s.get('id', f'segment-{i+1}')
        speaker = s.get('speaker_id') or 'unknown'
        text = s.get('text', '')
        if (
            not isinstance(ident, str)
            or not IDENT.fullmatch(ident)
            or ident in seen
            or not isinstance(speaker, str)
            or not IDENT.fullmatch(speaker)
        ):
            raise MaterialError('识别分句编号无效')
        if not isinstance(text, str) or len(text) > 20000:
            raise MaterialError('识别分句文字无效')
        start = s.get('start_ms', 0)
        end = s.get('end_ms', start)
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not 0 <= start <= end <= 1205000
        ):
            raise MaterialError('识别时间无效')
        seen.add(ident)
        result.append({'id': ident, 'speaker_id': speaker, 'text': text, 'start_ms': int(start), 'end_ms': int(end)})
    if sum(len(s['text']) for s in result) > 200000:
        raise MaterialError('识别文字过长')
    return result


def save_recognition(store, sid, segments, recognition_status):
    """流中替换累计临时快照；结束时冻结一次，任何后续重识别被拒绝。"""
    if recognition_status not in ('partial', 'complete', 'incomplete', 'failed'):
        raise MaterialError('识别状态无效')
    normalized = normalized_segments(segments)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT finalized FROM listening_originals WHERE session=?', (sid,)).fetchone()
        if not row or row['finalized']:
            raise MaterialError('原始识别已冻结，不能覆盖')
        if db.execute('SELECT 1 FROM listening_annotations WHERE session=? AND version>0', (sid,)).fetchone():
            raise MaterialError('已校订旁听不能重新识别')
        db.execute(
            'UPDATE listening_originals SET segments=?,recognition_status=?,finalized=? WHERE session=?',
            (json.dumps(normalized, ensure_ascii=False), recognition_status, int(recognition_status != 'partial'), sid),
        )
        if recognition_status != 'partial':
            # 兼容历史全文阅读；结构化原始来源另表保存，不从这里重建声音归属。
            for s in normalized:
                if s['text'].strip():
                    db.execute(
                        'INSERT INTO turns(session,role,text,seconds) VALUES(?,?,?,?)',
                        (sid, 'user', f"声音组 {s['speaker_id']}：{s['text']}", s['start_ms'] / 1000),
                    )


def detail(store, sid, can_edit=False, version=None, db=None):
    with nullcontext(db) if db is not None else store.db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=? AND mode='listening'", (sid,)).fetchone()
        original = conn.execute('SELECT * FROM listening_originals WHERE session=?', (sid,)).fetchone()
        if not row or not original:
            raise MaterialError('旁听记录不存在')
        session = dict(row)
        version = session['version'] if version is None else version
        ann = conn.execute(
            'SELECT * FROM listening_annotations WHERE session=? AND version=?', (sid, version)
        ).fetchone()
        if not ann:
            raise MaterialError('归属版本不存在')
        participants = json.loads(ann['participants'])
        bindings = json.loads(ann['bindings'])
        overrides = json.loads(ann['overrides'])
        originals = json.loads(original['segments'])
        segments = []
        groups = set(bindings)
        for s in originals:
            correction = overrides.get(s['id'], {})
            speaker = correction.get('speaker_id', s['speaker_id'])
            groups.add(speaker)
            groups.add(s['speaker_id'])
            segments.append(
                {
                    **s,
                    **correction,
                    'participant_id': bindings.get(speaker),
                    'original_text': s['text'],
                    'original_speaker_id': s['speaker_id'],
                }
            )
        session['version'] = version
        return {
            'session': session,
            'participants': participants,
            'speakers': [
                {'id': g, 'label': ('未知声音' if g == 'unknown' else f'声音组 {g}'), 'participant_id': bindings.get(g)}
                for g in sorted(groups)
            ],
            'segments': segments,
            'original_segments': originals,
            'recognition_status': original['recognition_status'],
            'identity_confirmation_required': not any(
                p['is_subject'] and p['id'] in {s['participant_id'] for s in segments} for p in participants
            ),
            'can_edit': bool(can_edit),
            'versions': [
                dict(r)
                for r in conn.execute(
                    'SELECT version,created_at FROM listening_annotations WHERE session=? ORDER BY version DESC', (sid,)
                )
            ],
        }


def annotate(store, sid, version, participants, bindings, overrides, editor):
    participants = validate_participants(participants)
    pids = {p['id'] for p in participants}
    if (
        not isinstance(bindings, dict)
        or len(bindings) > 200
        or not isinstance(overrides, dict)
        or len(overrides) > 20000
    ):
        raise MaterialError('声音归属校订无效')
    for speaker, pid in bindings.items():
        if (
            not isinstance(speaker, str)
            or not IDENT.fullmatch(speaker)
            or (pid is not None and (not isinstance(pid, str) or pid not in pids))
        ):
            raise MaterialError('声音组或参与者引用无效')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        current = detail(store, sid, db=db)
        if current['session']['version'] != version:
            raise ConflictError('归属已被修改，请刷新后再保存')
        if current['session']['status'] not in ('ended', 'interrupted') or current['recognition_status'] in (
            'pending',
            'partial',
        ):
            raise MaterialError('请结束录音并等待识别保存后校订')
        known = {s['id'] for s in current['original_segments']}
        groups = {s['speaker_id'] for s in current['original_segments']} | set(bindings)
        for ident, correction in overrides.items():
            if (
                ident not in known
                or not isinstance(correction, dict)
                or not correction
                or set(correction) - {'speaker_id', 'text'}
            ):
                raise MaterialError('分句校订引用无效')
            if 'speaker_id' in correction and (
                not isinstance(correction['speaker_id'], str) or correction['speaker_id'] not in groups
            ):
                raise MaterialError('请先添加目标声音组')
            if 'text' in correction and (not isinstance(correction['text'], str) or len(correction['text']) > 20000):
                raise MaterialError('校订文字过长或无效')
        if sum(len(c.get('text', '')) for c in overrides.values()) > 200000:
            raise MaterialError('校订文字过长')
        db.execute(
            'INSERT INTO listening_annotations(session,version,participants,bindings,overrides,editor) VALUES(?,?,?,?,?,?)',
            (
                sid,
                version + 1,
                json.dumps(participants, ensure_ascii=False),
                json.dumps(bindings),
                json.dumps(overrides, ensure_ascii=False),
                editor,
            ),
        )
        db.execute('UPDATE sessions SET version=version+1 WHERE id=?', (sid,))
    return detail(store, sid, True)
