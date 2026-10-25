"""按版本冻结编辑素材；原始录音/转写/导入资料始终是独立来源。"""
import hashlib
import json
import re
from contextlib import nullcontext


class MaterialError(ValueError):
    pass


class ConflictError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _human_text(text):
    """现有全文校订器带说话人标签；AI 段只作上下文，不可成为引用正文。"""
    lines = []; ai = False
    for line in text.splitlines():
        label = re.match(r'^([^：:\n]{1,80})[：:](.*)$', line)
        if label:
            ai = label[1].strip().lower() in ('ai', 'assistant', '采访者', '提问者')
        if not ai:
            lines.append(line)
    return '\n'.join(lines).strip()


def material(store, profile, kind, ident, db=None):
    with (nullcontext(db) if db is not None else store.db()) as conn:
        if kind == 'session':
            row = conn.execute('SELECT * FROM sessions WHERE id=? AND profile=?', (ident, profile)).fetchone()
            if not row or row['status'] not in ('ended', 'interrupted'):
                raise MaterialError('只能使用此档案已结束的聊天')
            turns = [dict(t) for t in conn.execute('SELECT * FROM turns WHERE session=? ORDER BY id', (ident,))]
            original = '\n\n'.join(t['text'] for t in turns if t['role'] == 'user')
            # version>0 的空校订是明确排除内容，不回退到原文。
            text = row['edited_text'] if row['version'] > 0 else original
            return {'kind': kind, 'id': ident, 'title': row['title'], 'revision': row['version'],
                    'text': text, 'original_text': original, 'turns': turns, 'has_audio': store.audio_path(ident).exists()}
        if kind == 'source':
            row = conn.execute('SELECT * FROM sources WHERE id=? AND profile=?', (ident, profile)).fetchone()
            if not row:
                raise MaterialError('资料不存在或不属于此档案')
            edit = conn.execute('SELECT * FROM material_revisions WHERE source=? ORDER BY version DESC LIMIT 1', (ident,)).fetchone()
            return {'kind': kind, 'id': ident, 'title': row['title'], 'revision': edit['version'] if edit else 0,
                    'text': edit['text'] if edit else row['text'], 'original_text': row['text'], 'has_audio': False}
        raise MaterialError('无效素材类型')


def list_materials(store, profile):
    with store.db() as db:
        refs = [{'kind': 'session', 'id': r['id']} for r in db.execute(
            "SELECT id FROM sessions WHERE profile=? AND status IN ('ended','interrupted') ORDER BY created_at,id", (profile,))]
        refs += [{'kind': 'source', 'id': r['id']} for r in db.execute('SELECT id FROM sources WHERE profile=? ORDER BY created_at,id', (profile,))]
        result = []
        for ref in refs:
            m = material(store, profile, ref['kind'], ref['id'], db)
            result.append({k: m[k] for k in ('kind', 'id', 'title', 'revision', 'has_audio')} | {'chars': len(m['text'])})
        return result


def snapshot(store, profile, refs, db=None, max_chars=40000):
    if not refs or len(refs) > 20:
        raise MaterialError('请选择1至20份素材')
    normalized = sorted({(r['kind'], r['id']) for r in refs})
    units = []; selected = []
    with (nullcontext(db) if db is not None else store.db()) as conn:
        for kind, ident in normalized:
            m = material(store, profile, kind, ident, conn)
            selected.append({'kind': kind, 'id': ident, 'revision': m['revision'], 'text_hash': digest(m['text'])})
            base = {'source_kind': kind, 'source_id': ident, 'revision': m['revision'], 'title': m['title'],
                    'turn_id': None, 'seconds': None, 'context': ''}
            if kind == 'session' and m['revision'] == 0:
                context = ''
                for turn in m['turns']:
                    if turn['role'] == 'assistant':
                        context = turn['text']
                    elif turn['role'] == 'user' and turn['text'].strip():
                        units.append({**base, 'unit_id': f"session:{ident}:turn:{turn['id']}",
                                      'text': turn['text'], 'turn_id': turn['id'], 'seconds': turn['seconds'],
                                      'context': context, 'origin': 'original_transcript'})
            elif m['text'].strip():
                text = _human_text(m['text']) if kind == 'session' else m['text']
                if text:
                    units.append({**base, 'unit_id': f"{kind}:{ident}:revision:{m['revision']}", 'text': text,
                                  'origin': 'human_revision' if m['revision'] else 'imported_source'})
    if not units:
        raise MaterialError('所选素材没有可整理的讲述内容')
    if sum(len(u['text']) + len(u['context']) for u in units) > max_chars:
        raise MaterialError(f'所选素材超过{max_chars}字符，请分章或减少选择；没有截断原文')
    return {'materials': units, 'sources': [{'kind': k, 'id': i} for k, i in normalized],
            'selected_versions': selected, 'source_hash': digest({'selected': selected, 'units': units})}


def stale(store, profile, refs, source_hash, db=None, max_chars=40000):
    try:
        return snapshot(store, profile, refs, db, max_chars=max_chars)['source_hash'] != source_hash
    except MaterialError:
        return True


def item_stale(store, item, db=None):
    units = item.get('source_snapshot') or []
    if not units or not item.get('input_hash'):
        return False
    refs = [{'kind': kind, 'id': ident} for kind, ident in
            sorted({(u['source_kind'], u['source_id']) for u in units})]
    return stale(store, item['profile'], refs, item['input_hash'], db, max_chars=120000)


def revise(store, profile, kind, ident, text, version, editor, title=None):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        current = material(store, profile, kind, ident, db)
        if current['revision'] != version:
            raise ConflictError('素材已被修改，请刷新后再保存')
        if kind == 'session':
            db.execute('UPDATE sessions SET edited_text=?,title=?,version=version+1 WHERE id=? AND version=?',
                       (text, title if title is not None else current['title'], ident, version))
            db.execute('INSERT INTO revisions(session,version,text,editor) VALUES(?,?,?,?)', (ident, version+1, text, editor))
        else:
            db.execute('INSERT INTO material_revisions(source,version,text,editor) VALUES(?,?,?,?)', (ident, version+1, text, editor))
    return material(store, profile, kind, ident)


def checked_evidence(citations, units, paragraph=None):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 30:
        raise MaterialError('缺少有效来源引用')
    known = {u['unit_id']: u for u in units}
    result = []
    for c in citations:
        if not isinstance(c, dict):
            raise MaterialError('引用格式无效')
        u = known.get(c.get('unit_id')); quote = c.get('quote')
        if not u or not isinstance(quote, str) or not quote.strip() or len(quote) > 4000 or quote not in u['text']:
            raise MaterialError('引用不属于本次素材快照')
        if len(quote) < 4 and quote != u['text'].strip():
            raise MaterialError('引用过短，无法可靠定位')
        e = {k: u[k] for k in ('unit_id', 'source_kind', 'source_id', 'revision', 'title', 'turn_id', 'seconds', 'origin')}
        e['quote'] = quote
        if u['source_kind'] == 'session':
            e['session_id'] = u['source_id']
        if paragraph is not None:
            e['paragraph'] = paragraph
        result.append(e)
    return result
