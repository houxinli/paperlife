"""独立数据库与私有音频存储，不复用交易账户权限。"""

import json
import hashlib
import secrets
import time
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        from .schema import migrate

        with self.db() as db:
            # WAL 让读者与后台写入互不阻塞；该模式持久记录在数据库文件中。
            db.execute('PRAGMA journal_mode=WAL')
            migrate(db)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'life.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_profile(self, user, name, context, voice_dialect="sichuan"):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                'INSERT INTO profiles(id,name,context,owner,voice_dialect) VALUES(?,?,?,?,?)',
                (ident, name, context, user, voice_dialect),
            )
            db.execute('INSERT INTO members VALUES(?,?,?)', (ident, user, 'owner'))
            if context:
                db.execute(
                    'INSERT INTO sources(id,profile,title,text) VALUES(?,?,?,?)',
                    (uuid.uuid4().hex, ident, '导入的原始访谈', context),
                )
        return self.profile(ident, user)

    def profile(self, ident, user):
        with self.db() as db:
            row = db.execute(
                'SELECT p.*, m.role FROM profiles p JOIN members m ON p.id=m.profile WHERE p.id=? AND m.user=?',
                (ident, user),
            ).fetchone()
            return dict(row) if row else None

    def profiles(self, user):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    'SELECT p.id,p.name,p.created_at,m.role FROM profiles p JOIN members m ON p.id=m.profile WHERE m.user=? ORDER BY p.created_at',
                    (user,),
                )
            ]

    def update_profile(self, ident, name, context, voice_dialect=None):
        with self.db() as db:
            db.execute(
                'UPDATE profiles SET name=?,context=?,voice_dialect=COALESCE(?,voice_dialect) WHERE id=?',
                (name, context, voice_dialect, ident),
            )
            # 空档案第一次填写背景时冻结为来源；之后的编辑不改写这个快照。
            if context and not db.execute('SELECT 1 FROM sources WHERE profile=?', (ident,)).fetchone():
                db.execute(
                    'INSERT INTO sources(id,profile,title,text) VALUES(?,?,?,?)',
                    (uuid.uuid4().hex, ident, '首次导入的原始访谈', context),
                )

    def members(self, profile):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT user,role FROM members WHERE profile=?', (profile,))]

    def set_member(self, profile, user, role):
        with self.db() as db:
            db.execute(
                'INSERT INTO members VALUES(?,?,?) ON CONFLICT(profile,user) DO UPDATE SET role=excluded.role',
                (profile, user, role),
            )

    def remove_member(self, profile, user):
        with self.db() as db:
            db.execute('DELETE FROM members WHERE profile=? AND user=?', (profile, user))

    def sessions(self, profile):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    'SELECT id,title,status,created_at,version,mode FROM sessions WHERE profile=? ORDER BY created_at DESC',
                    (profile,),
                )
            ]

    def sessions_last_day(self, user):
        with self.db() as db:
            return db.execute(
                "SELECT count(*) FROM sessions WHERE user=? AND created_at>datetime('now','-1 day')", (user,)
            ).fetchone()[0]

    def quality(self, session):
        with self.db() as db:
            return {
                'flags': [
                    dict(r)
                    for r in db.execute(
                        'SELECT turn,code,reason,ask_now,seconds FROM quality_flags WHERE session=?', (session,)
                    )
                ],
                'metrics': [
                    dict(r) for r in db.execute('SELECT kind,value_ms FROM voice_metrics WHERE session=?', (session,))
                ],
            }

    def revisions(self, session):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    'SELECT version,text,created_at FROM revisions WHERE session=? ORDER BY version DESC', (session,)
                )
            ]

    def create_session(self, profile, user, topic):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                'INSERT INTO sessions(id,profile,user,topic,title,status) VALUES(?,?,?,?,?,?)',
                (ident, profile, user, topic, topic, 'connecting'),
            )
        return self.session(ident)

    def session(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (ident,)).fetchone()
            return dict(row) if row else None

    def add_turn(self, ident, role, text, seconds):
        if text.strip():
            with self.db() as db:
                return db.execute(
                    'INSERT INTO turns(session,role,text,seconds) VALUES(?,?,?,?)', (ident, role, text, seconds)
                ).lastrowid

    def finish_session(self, ident, status):
        with self.db() as db:
            db.execute('UPDATE sessions SET status=? WHERE id=?', (status, ident))

    def abandon_session(self, ident):
        """票据未被使用或连接在开始前失败：仍处于 connecting 的会话收尾为 interrupted。"""
        with self.db() as db:
            db.execute("UPDATE sessions SET status='interrupted' WHERE id=? AND status='connecting'", (ident,))

    def abandon_stale_connections(self, minutes=5):
        # 票据 60 秒过期、上游连接最多约 50 秒；超过这个时间仍在 connecting 的会话不会再开始。
        with self.db() as db:
            return db.execute(
                "UPDATE sessions SET status='interrupted' WHERE status='connecting' AND created_at<datetime('now',?)",
                (f'-{int(minutes)} minutes',),
            ).rowcount

    def turns(self, ident):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute('SELECT id,role,text,seconds FROM turns WHERE session=? ORDER BY id', (ident,))
            ]

    def record_preferences(self, session_id, turn_id, text, *, naming_target=None):
        """仅记录已经落库的最终 user 原文；重复/撤销后重放不会复活偏好。"""
        from .preferences import extract_preferences

        requested = extract_preferences(text, naming_target=naming_target)
        changed = []
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            source = db.execute(
                "SELECT s.profile,t.text FROM turns t JOIN sessions s ON s.id=t.session WHERE t.id=? AND t.session=? AND t.role=? AND s.mode='dialogue'",
                (turn_id, session_id, 'user'),
            ).fetchone()
            if not source or source['text'] != text:
                return []
            # Only realtime's first final answer to its opening naming question
            # consumes this opportunity. Failed connects/interim ASR do not.
            if naming_target == 'interviewer_name':
                db.execute('UPDATE profiles SET interviewer_name_asked=1 WHERE id=?', (source['profile'],))
                db.execute(
                    'INSERT OR IGNORE INTO interview_naming_answers(session,profile,turn) VALUES(?,?,?)',
                    (session_id, source['profile'], turn_id),
                )
            processed = db.execute(
                'INSERT OR IGNORE INTO interview_preference_turns(session,turn) VALUES(?,?)', (session_id, turn_id)
            )
            if processed.rowcount == 0:
                return []
            for key, value in requested.items():
                if db.execute(
                    'SELECT 1 FROM interview_preferences WHERE session=? AND turn=? AND key=?',
                    (session_id, turn_id, key),
                ).fetchone():
                    continue
                current = db.execute(
                    "SELECT value FROM interview_preferences WHERE profile=? AND key=? AND status='active'",
                    (source['profile'], key),
                ).fetchone()
                if current and current['value'] == value:
                    continue
                db.execute(
                    "UPDATE interview_preferences SET status='superseded',updated_at=CURRENT_TIMESTAMP WHERE profile=? AND key=? AND status='active'",
                    (source['profile'], key),
                )
                result = db.execute(
                    'INSERT INTO interview_preferences(profile,key,value,session,turn,source_text) VALUES(?,?,?,?,?,?)',
                    (source['profile'], key, value, session_id, turn_id, text[:300]),
                )
                changed.append(
                    dict(
                        db.execute(
                            'SELECT id,profile,key,value,session,turn,source_text,created_at FROM interview_preferences WHERE id=?',
                            (result.lastrowid,),
                        ).fetchone()
                    )
                )
        return changed

    def preferences(self, profile_id):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,profile,key,value,session,turn,source_text,created_at FROM interview_preferences WHERE profile=? AND status='active' ORDER BY id",
                    (profile_id,),
                )
            ]

    def revoke_preference(self, profile_id, preference_id):
        with self.db() as db:
            result = db.execute(
                "UPDATE interview_preferences SET status='revoked',updated_at=CURRENT_TIMESTAMP WHERE id=? AND profile=? AND status='active'",
                (preference_id, profile_id),
            )
            return result.rowcount == 1

    def provider(self):
        path = self.root / 'provider.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        for field, key in [
            ('app_id', 'LIFE_VOLC_APP_ID'),
            ('access_token', 'LIFE_VOLC_ACCESS_TOKEN'),
            ('api_key', 'LIFE_VOLC_API_KEY'),
        ]:
            if os.environ.get(key):
                data[field] = os.environ[key]
        return data

    def configure(self, data):
        # 网页设置保存在挂载的私有运行时目录，不进入仓库或日志。
        target = self.root / 'provider.json'
        temp = self.root / (uuid.uuid4().hex + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(temp, target)

    def audio_path(self, ident, track='user'):
        if not ident or any(c not in '0123456789abcdef' for c in ident) or len(ident) != 32:
            raise ValueError('Invalid session')
        return self.root / f'{ident}-{track}.wav'

    def create_link(self, profile):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = time.time() + 180 * 86400
        with self.db() as db:
            result = db.execute(
                'INSERT INTO narrator_links(profile,token_hash,expires) VALUES(?,?,?)', (profile, digest, expires)
            )
            return {'id': result.lastrowid, 'token': token, 'expires': expires}

    def links(self, profile):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    'SELECT id,expires,revoked,created_at FROM narrator_links WHERE profile=? ORDER BY id DESC',
                    (profile,),
                )
            ]

    def revoke_link(self, profile, link_id):
        with self.db() as db:
            db.execute('UPDATE narrator_links SET revoked=1 WHERE id=? AND profile=?', (link_id, profile))

    def link(self, token=None, link_id=None):
        with self.db() as db:
            if token:
                row = db.execute(
                    'SELECT * FROM narrator_links WHERE token_hash=? AND revoked=0 AND expires>?',
                    (hashlib.sha256(token.encode()).hexdigest(), time.time()),
                ).fetchone()
            else:
                row = db.execute(
                    'SELECT * FROM narrator_links WHERE id=? AND revoked=0 AND expires>?', (link_id, time.time())
                ).fetchone()
            if not row:
                return None
            profile = db.execute('SELECT * FROM profiles WHERE id=?', (row['profile'],)).fetchone()
            return {**dict(row), 'profile_data': dict(profile)} if profile else None

    def item(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (ident,)).fetchone()
            if not row:
                return None
            result = dict(row)
            for key, fallback in [
                ('source_ids', '[]'),
                ('evidence', '[]'),
                ('generation_metadata', '{}'),
                ('source_snapshot', '[]'),
            ]:
                result[key] = json.loads(result.get(key) or fallback)
            from .materials import item_stale

            result['stale'] = item_stale(self, result, db)
            return result

    def sources(self, profile):
        with self.db() as db:
            return [
                dict(r) for r in db.execute('SELECT * FROM sources WHERE profile=? ORDER BY created_at', (profile,))
            ]

    def owns_sources(self, profile, ids):
        """ids 中每一项都是本档案的导入资料或聊天。"""
        with self.db() as db:
            return all(
                db.execute(
                    'SELECT 1 FROM sources WHERE id=? AND profile=? UNION SELECT 1 FROM sessions WHERE id=? AND profile=?',
                    (ident, profile, ident, profile),
                ).fetchone()
                for ident in ids
            )

    def items(self, profile, statuses=None, kinds=None):
        query = 'SELECT id FROM items WHERE profile=?'
        params = [profile]
        for column, values in (('status', statuses), ('kind', kinds)):
            if values is not None:
                query += f' AND {column} IN ({",".join("?" * len(values))})'
                params += list(values)
        with self.db() as db:
            ids = [r['id'] for r in db.execute(query + ' ORDER BY created_at,id', params)]
        return [self.item(i) for i in ids]

    def edit_item(self, ident, data, editor):
        """乐观锁更新条目并追加修改历史；过期草稿不能直接确认。"""
        from .materials import ConflictError, item_stale

        item = self.item(ident)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if (
                item['status'] == 'draft'
                and data['status'] in ('confirmed', 'open', 'resolved')
                and item_stale(self, item, db)
            ):
                raise ConflictError('素材已经校订，请使用新整理的条目；旧草稿可保留或归档')
            updated = db.execute(
                'UPDATE items SET title=?,body=?,answer=?,status=?,source_ids=?,time_text=?,people=?,place=?,certainty=?,version=version+1 WHERE id=? AND version=?',
                (
                    data['title'],
                    data['body'],
                    data['answer'],
                    data['status'],
                    json.dumps(data['source_ids']),
                    data['time_text'],
                    data['people'],
                    data['place'],
                    data['certainty'],
                    ident,
                    data['version'],
                ),
            )
            if not updated.rowcount:
                raise ConflictError('条目已有新版本，请重新打开')
            db.execute(
                'INSERT INTO item_revisions(item,version,snapshot,editor) VALUES(?,?,?,?)',
                (ident, item['version'], json.dumps(item, ensure_ascii=False), editor),
            )
        return self.item(ident)

    def item_revisions(self, ident):
        with self.db() as db:
            return [
                {**dict(r), 'snapshot': json.loads(r['snapshot'])}
                for r in db.execute(
                    'SELECT version,snapshot,created_at FROM item_revisions WHERE item=? ORDER BY version DESC',
                    (ident,),
                )
            ]

    def pending_count(self, profile):
        """待人工确认的条目与章节草稿数量。"""
        with self.db() as db:
            return db.execute(
                "SELECT (SELECT count(*) FROM items WHERE profile=? AND status='draft')+(SELECT count(*) FROM chapter_versions v JOIN chapters c ON c.id=v.chapter WHERE c.profile=? AND v.status='draft')",
                (profile, profile),
            ).fetchone()[0]

    def history(self, profile, offset, limit):
        with self.db() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    'SELECT id,title,status,created_at,version,mode FROM sessions WHERE profile=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                    (profile, limit + 1, offset),
                )
            ]
        return {'sessions': rows[:limit], 'has_more': len(rows) > limit}

    def processing(self, profile):
        """旧队列的历史记录与新队列的素材整理任务，按时间倒序合并。"""
        with self.db() as db:
            old = [
                dict(r)
                for r in db.execute(
                    "SELECT session,status,attempts,progress,total,error,created_at,completed_at FROM processing_jobs WHERE profile=? AND status!='superseded' ORDER BY created_at DESC",
                    (profile,),
                )
            ]
            new = [
                dict(r)
                for r in db.execute(
                    "SELECT COALESCE(source_session,json_extract(snapshot,'$.sources[0].id')) session,status,attempts,CASE WHEN status='completed' THEN 1 ELSE 0 END progress,1 total,error,created_at,completed_at FROM writing_jobs WHERE profile=? AND kind='material' ORDER BY created_at DESC",
                    (profile,),
                )
            ]
        return sorted(old + new, key=lambda r: r['created_at'], reverse=True)

    def job_profile(self, job):
        with self.db() as db:
            row = db.execute('SELECT profile FROM writing_jobs WHERE id=?', (job,)).fetchone()
            return row['profile'] if row else None

    def add_item(self, profile, data):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                'INSERT INTO items(id,profile,kind,title,body,answer,status,source_ids,time_text,people,place,certainty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    ident,
                    profile,
                    data['kind'],
                    data['title'],
                    data['body'],
                    data.get('answer', ''),
                    data.get('status', 'draft'),
                    json.dumps(data.get('source_ids', [])),
                    data.get('time_text', ''),
                    data.get('people', ''),
                    data.get('place', ''),
                    data.get('certainty', 'unverified'),
                ),
            )
        return self.item(ident)

    def suggest_questions(self, session_id):
        import re

        session = self.session(session_id)
        if not session:
            return
        for turn in self.turns(session_id):
            text = turn['text']
            if turn['role'] == 'user' and re.search(r'不确定|记不清|不知道.*字|[（(]音[）)]|听不清', text):
                self.add_item(
                    session['profile'],
                    {
                        'kind': 'question',
                        'title': '这段回忆有待确认',
                        'body': text[:2000],
                        'status': 'open',
                        'source_ids': [session_id],
                    },
                )

    def record_quality(self, session, turn, text, seconds):
        from datetime import datetime, timezone
        from .quality import inspect_transcript

        flags = inspect_transcript(text, datetime.now(timezone.utc).year)
        with self.db() as db:
            for f in flags:
                db.execute(
                    'INSERT OR IGNORE INTO quality_flags VALUES(?,?,?,?,?,?)',
                    (session, turn, f['code'], f['reason'], int(f['ask_now']), seconds),
                )
        return flags

    def metric(self, session, kind, value_ms):
        if kind not in ('connect_ms', 'response_first_audio_ms') or not 0 <= value_ms <= 120000:
            return
        with self.db() as db:
            db.execute('INSERT INTO voice_metrics(session,kind,value_ms) VALUES(?,?,?)', (session, kind, value_ms))
