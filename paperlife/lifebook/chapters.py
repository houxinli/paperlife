"""章节版本与人工发布；确认版本不可变，当前出版物仅保存指针。"""

import json
import uuid

from .materials import ConflictError, stale


def chapter(store, ident, db=None):
    if db is not None:
        row = db.execute('SELECT * FROM chapters WHERE id=?', (ident,)).fetchone()
    else:
        with store.db() as conn:
            row = conn.execute('SELECT * FROM chapters WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def create(store, profile, title, brief):
    ident = uuid.uuid4().hex
    with store.db() as db:
        db.execute('INSERT INTO chapters(id,profile,title,brief) VALUES(?,?,?,?)', (ident, profile, title, brief))
    return chapter(store, ident)


def version(store, row, profile, db=None):
    data = dict(row)
    for key in ('paragraphs', 'evidence', 'sources', 'source_refs', 'metadata'):
        data[key] = json.loads(data[key])
    data['stale'] = stale(store, profile, data['source_refs'], data['source_hash'], db)
    return data


def detail(store, ident, drafts=True):
    with store.db() as db:
        c = chapter(store, ident, db)
        if not c:
            return None
        rows = db.execute('SELECT * FROM chapter_versions WHERE chapter=? ORDER BY number DESC', (ident,)).fetchall()
        versions = [version(store, r, c['profile'], db) for r in rows if drafts or r['status'] == 'confirmed']
        if not drafts and not c['published_version_id']:
            return None
        return {'chapter': c, 'versions': versions}


def published(store, profile):
    with store.db() as db:
        rows = db.execute(
            "SELECT v.* FROM chapters c JOIN chapter_versions v ON c.published_version_id=v.id WHERE c.profile=? AND v.status='confirmed' ORDER BY c.created_at,c.id",
            (profile,),
        ).fetchall()
        # 原始素材版本属于证据链；不能用最新校订覆盖旧稿的来源快照。
        return [version(store, r, profile, db) for r in rows]


def listing(store, profile, drafts=True):
    with store.db() as db:
        result = []
        for row in db.execute('SELECT * FROM chapters WHERE profile=? ORDER BY created_at,id', (profile,)).fetchall():
            c = dict(row)
            if not drafts and not c['published_version_id']:
                continue
            versions = db.execute(
                'SELECT * FROM chapter_versions WHERE chapter=? ORDER BY number DESC', (c['id'],)
            ).fetchall()
            visible = [v for v in versions if drafts or v['status'] == 'confirmed']
            pub = next((v for v in versions if v['id'] == c['published_version_id']), None)
            latest = visible[0] if visible else None
            result.append(
                {
                    **c,
                    'published_version': pub['number'] if pub else None,
                    'latest_version': latest['number'] if latest else None,
                    'draft_count': sum(v['status'] == 'draft' for v in visible),
                    'stale': stale(store, profile, json.loads(latest['source_refs']), latest['source_hash'], db)
                    if latest
                    else False,
                }
            )
        return result


def edit(store, ident, vid, title, body, edit_version):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM chapter_versions WHERE id=? AND chapter=?', (vid, ident)).fetchone()
        if not row or row['status'] != 'draft' or row['edit_version'] != edit_version:
            raise ConflictError('只能编辑最新草稿；已确认版本保持不变')
        metadata = json.loads(row['metadata'])
        metadata['human_edited'] = True
        metadata['audit_scope'] = 'model_draft_before_human_edit'
        # 人工改段落以后不能继续声称旧引文与新段落逐段对齐。
        evidence = json.loads(row['evidence'])
        for e in evidence:
            e.pop('paragraph', None)
        paragraphs = [{'text': p, 'citations': []} for p in body.split('\n\n') if p.strip()]
        db.execute(
            'UPDATE chapter_versions SET title=?,body=?,paragraphs=?,evidence=?,edit_version=edit_version+1,metadata=? WHERE id=?',
            (
                title,
                body,
                json.dumps(paragraphs, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False),
                vid,
            ),
        )
        updated = db.execute('SELECT * FROM chapter_versions WHERE id=?', (vid,)).fetchone()
        return version(store, updated, chapter(store, ident, db)['profile'], db)


def confirm(store, ident, vid, edit_version, user):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        c = chapter(store, ident, db)
        row = db.execute('SELECT * FROM chapter_versions WHERE chapter=? AND id=?', (ident, vid)).fetchone()
        if not row or row['status'] != 'draft' or row['edit_version'] != edit_version:
            raise ConflictError('草稿已变化或已确认，请刷新核对')
        if row['base_published_id'] != c['published_version_id']:
            raise ConflictError('已有另一版本收入书中，请以当前版为基础重新生成')
        if stale(store, c['profile'], json.loads(row['source_refs']), row['source_hash'], db):
            raise ConflictError('来源素材已校订，请生成新版本再确认；已确认内容不变')
        db.execute(
            "UPDATE chapter_versions SET status='confirmed',confirmed_by=?,confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
            (user, vid),
        )
        db.execute('UPDATE chapters SET published_version_id=? WHERE id=?', (vid, ident))
        return version(
            store, db.execute('SELECT * FROM chapter_versions WHERE id=?', (vid,)).fetchone(), c['profile'], db
        )
