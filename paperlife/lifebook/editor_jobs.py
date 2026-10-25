"""持久化编辑队列：素材整理与跨会话写作解耦，产物全部待人工确认。"""
import asyncio
import json
import time
import uuid

from . import chapters
from .materials import MaterialError, ConflictError, checked_evidence, digest, snapshot


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _public_job(row):
    return {key: row[key] for key in ('id', 'profile', 'chapter', 'kind', 'status', 'stage', 'attempts', 'error', 'version_id', 'created_at', 'completed_at')}


def jobs(store, profile):
    with store.db() as db:
        return [_public_job(r) for r in db.execute('SELECT * FROM writing_jobs WHERE profile=? ORDER BY created_at DESC,rowid DESC LIMIT 100', (profile,))]


def _enqueue(store, profile, kind, refs, requester, chapter=None, brief=None):
    from .editor_models import public_config
    configuration = public_config(store, False)
    configuration.pop('can_configure', None)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        data = snapshot(store, profile, refs, db, max_chars=120000 if kind == 'material' else 40000)
        count = 0
        if chapter:
            c = chapters.chapter(store, chapter, db)
            if not c or c['profile'] != profile:
                raise MaterialError('章节不属于当前档案')
            count = db.execute('SELECT count(*) FROM chapter_versions WHERE chapter=?', (chapter,)).fetchone()[0]
            prior = db.execute('SELECT title,body FROM chapter_versions WHERE id=?', (c['published_version_id'],)).fetchone()
            data.update({'chapter_title': c['title'], 'brief': c['brief'] if brief is None else brief,
                         'base_published_id': c['published_version_id'],
                         'previous_version': dict(prior) if prior else None})
        data['configuration'] = configuration
        key = digest({'profile': profile, 'kind': kind, 'chapter': chapter,
                      'snapshot': data, 'existing_versions': count})
        ident = uuid.uuid4().hex
        db.execute('INSERT OR IGNORE INTO writing_jobs(id,profile,chapter,kind,requester,request_key,snapshot) VALUES(?,?,?,?,?,?,?)',
                   (ident, profile, chapter, kind, requester, key, _json(data)))
        return _public_job(db.execute('SELECT * FROM writing_jobs WHERE request_key=?', (key,)).fetchone())


def enqueue_material(store, profile, refs, requester):
    return _enqueue(store, profile, 'material', refs, requester)


def enqueue_session(store, session_id):
    s = store.session(session_id)
    if not s or s['status'] not in ('ended', 'interrupted'):
        return None
    with store.db() as db:
        p = db.execute('SELECT owner FROM profiles WHERE id=?', (s['profile'],)).fetchone()
    if not p:
        return None
    try:
        return enqueue_material(store, s['profile'], [{'kind': 'session', 'id': session_id}], p['owner'])
    except MaterialError:
        return None


def enqueue_chapter(store, profile, chapter, refs, requester, brief=None):
    return _enqueue(store, profile, 'chapter', refs, requester, chapter, brief)


def retry(store, ident):
    with store.db() as db:
        db.execute("UPDATE writing_jobs SET status='pending',attempts=0,next_run=0,error='',lease_until=0 WHERE id=? AND status='failed'", (ident,))
        row = db.execute('SELECT * FROM writing_jobs WHERE id=?', (ident,)).fetchone()
        return _public_job(row) if row else None


def claim(store):
    now = time.time(); token = uuid.uuid4().hex
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE writing_jobs SET status='failed',error='整理中断，请重试' WHERE status='running' AND lease_until<? AND attempts>=3", (now,))
        row = db.execute("SELECT * FROM writing_jobs WHERE attempts<3 AND ((status='pending' AND next_run<=?) OR (status='running' AND lease_until<?)) ORDER BY created_at,rowid LIMIT 1", (now, now)).fetchone()
        if not row:
            return None
        db.execute("UPDATE writing_jobs SET status='running',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?", (now+600, token, row['id']))
        return {**dict(row), 'lease_token': token, 'attempts': row['attempts']+1}


def _chunks(units, limit=5000):
    current = []; size = 0
    for unit in units:
        for offset in range(0, len(unit['text']), 3500):
            part = {**unit, 'text': unit['text'][offset:offset+3500], 'context': unit['context'][:600]}
            n = len(_json(part))
            if current and size+n > limit:
                yield current; current = []; size = 0
            current.append(part); size += n
    if current:
        yield current


def _story(output, units):
    if not isinstance(output, dict) or not isinstance(output.get('title'), str) or not 1 <= len(output['title'].strip()) <= 200:
        raise MaterialError('无效章节标题')
    paragraphs = output.get('paragraphs')
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 30:
        raise MaterialError('章节正文为空或过长')
    evidence = []; body = []
    for index, paragraph in enumerate(paragraphs, 1):
        text = paragraph.get('text') if isinstance(paragraph, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise MaterialError('无效章节段落')
        evidence.extend(checked_evidence(paragraph.get('citations'), units, index)); body.append(text)
    if len('\n\n'.join(body)) > 30000:
        raise MaterialError('章节正文过长')
    return {'title': output['title'], 'paragraphs': paragraphs, 'body': '\n\n'.join(body), 'evidence': evidence}


def _extraction(output, units):
    items = output.get('items') if isinstance(output, dict) else None
    if not isinstance(items, list) or len(items) > 30:
        raise MaterialError('无效素材整理结果')
    result = []
    for item in items:
        if item.get('kind') not in ('person', 'event', 'fact', 'question'):
            raise MaterialError('无效条目类型')
        for key, limit in [('title', 200), ('body', 4000), ('time_text', 200), ('people', 500), ('place', 300)]:
            value = item.get(key, '')
            if not isinstance(value, str) or len(value) > limit or (key in ('title', 'body') and not value.strip()):
                raise MaterialError('无效条目字段')
        result.append({**item, 'evidence': checked_evidence(item.get('citations'), units)})
    return result


async def process_one(store, generator=None):
    from .editor_models import generate_editor
    job = claim(store)
    if not job:
        return False
    data = json.loads(job['snapshot']); checkpoint = json.loads(job['checkpoint'])

    def save(stage):
        with store.db() as db:
            updated = db.execute('UPDATE writing_jobs SET checkpoint=?,stage=?,lease_until=? WHERE id=? AND lease_token=? AND status=\'running\'',
                                 (_json(checkpoint), stage, time.time()+600, job['id'], job['lease_token']))
            if updated.rowcount != 1:
                raise ConflictError('任务租约已失效')

    async def call(stage, payload, key=None):
        key = key or stage
        if key not in checkpoint:
            save(stage)
            response = await (generator(stage, payload) if generator else generate_editor(store, stage, payload))
            metadata = response.get('metadata', {})
            # 仅白名单追踪字段写入草稿；不持久化供应商响应或认证头。
            metadata = {k: metadata.get(k, '') for k in ('provider', 'model', 'prompt_version', 'prompt_hash')}
            checkpoint[key] = {'output': response['output'], 'metadata': metadata}
        return checkpoint[key]['output']

    try:
        access = store.profile(job['profile'], job['requester'])
        if not access or access['role'] == 'reader':
            raise MaterialError('任务发起人已无编辑权限')
        if job['kind'] == 'material':
            outputs = []
            for index, units in enumerate(_chunks(data['materials'])):
                raw = await call('extract', {'materials': units}, f'extract:{index}')
                outputs.extend(_extraction(raw, units)); save('extract')
            final = None
        else:
            payload = {k: v for k, v in data.items() if k != 'configuration'}
            payload['feedback'] = checkpoint.get('feedback', [])
            outline = await call('outline', payload)
            if not isinstance(outline, dict) or not isinstance(outline.get('outline'), list) or not outline['outline']:
                raise MaterialError('章节大纲为空')
            save('outline'); payload['outline'] = outline['outline']
            raw = await call('write', payload); _story(raw, data['materials']); save('write')
            payload['draft'] = raw
            for audit_key, polish in [('audit', True), ('final_audit', False)]:
                audit = await call('audit', payload, audit_key)
                if audit.get('ok') is not True or not isinstance(audit.get('issues'), list) or audit['issues']:
                    checkpoint['feedback'] = audit.get('issues', ['请重新核对事实来源'])
                    for key in ('write', 'audit', 'polish', 'final_audit'):
                        checkpoint.pop(key, None)
                    save('needs_revision')
                    raise MaterialError('事实核对未通过')
                save(audit_key)
                if polish:
                    polished = await call('polish', payload)
                    _story(polished, data['materials']); save('polish'); payload['draft'] = polished
            final = _story(payload['draft'], data['materials'])
        metadata = {'steps': {k: v['metadata'] for k, v in checkpoint.items() if isinstance(v, dict) and 'metadata' in v},
                    'source_hash': data['source_hash'], 'human_edited': False,
                    'notice': '模型核对不替代人工确认，引用存在不等于事实已被外部证实'}
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT * FROM writing_jobs WHERE id=?', (job['id'],)).fetchone()
            if active['status'] != 'running' or active['lease_token'] != job['lease_token']:
                return True
            version_id = None
            if job['kind'] == 'material':
                model = ', '.join(sorted({m['model'] for m in metadata['steps'].values()}))
                for item in outputs:
                    source_ids = sorted({e['source_id'] for e in item['evidence']})
                    db.execute("INSERT INTO items(id,profile,kind,title,body,status,source_ids,time_text,people,place,evidence,generated_job,input_hash,model,generation_metadata,source_snapshot) VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?)",
                               (uuid.uuid4().hex, job['profile'], item['kind'], item['title'], item['body'], _json(source_ids),
                                item.get('time_text',''), item.get('people',''), item.get('place',''), _json(item['evidence']), job['id'],
                                data['source_hash'], model, _json(metadata), _json(data['materials'])))
            else:
                version_id = uuid.uuid4().hex
                number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM chapter_versions WHERE chapter=?', (job['chapter'],)).fetchone()[0]
                db.execute('INSERT INTO chapter_versions(id,chapter,number,title,body,paragraphs,evidence,sources,source_refs,source_hash,metadata,base_published_id,job) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (version_id, job['chapter'], number, final['title'], final['body'], _json(final['paragraphs']), _json(final['evidence']),
                            _json(data['materials']), _json(data['sources']), data['source_hash'], _json(metadata), data['base_published_id'], job['id']))
            db.execute("UPDATE writing_jobs SET status='completed',stage='completed',version_id=?,error='',lease_until=0,completed_at=CURRENT_TIMESTAMP WHERE id=?", (version_id, job['id']))
    except asyncio.CancelledError:
        with store.db() as db:
            db.execute("UPDATE writing_jobs SET status='pending',attempts=attempts-1,lease_until=0 WHERE id=? AND lease_token=? AND status='running'", (job['id'], job['lease_token']))
        raise
    except Exception:
        with store.db() as db:
            db.execute("UPDATE writing_jobs SET status=?,next_run=?,lease_until=0,error=? WHERE id=? AND lease_token=? AND status='running'",
                       ('failed' if job['attempts'] >= 3 else 'pending', time.time()+30*job['attempts'],
                        '生成未完成：模型响应、来源引用或事实核对未通过。可重试；原始资料和已确认内容保持不变。', job['id'], job['lease_token']))
    return True


# 只扫描新结束会话；已有旧处理任务视为已导入的历史成果，不重新调用模型。
def enqueue_missing(store):
    with store.db() as db:
        ids = [r['id'] for r in db.execute("SELECT s.id FROM sessions s WHERE s.status IN ('ended','interrupted') AND EXISTS(SELECT 1 FROM turns t WHERE t.session=s.id AND t.role='user' AND trim(t.text)!='') AND NOT EXISTS(SELECT 1 FROM processing_jobs p WHERE p.session=s.id) AND NOT EXISTS(SELECT 1 FROM writing_jobs j WHERE j.kind='material' AND j.profile=s.profile AND json_extract(j.snapshot,'$.sources[0].id')=s.id)")]
    for ident in ids:
        enqueue_session(store, ident)
