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
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, context TEXT NOT NULL, owner INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS members(profile TEXT, user INTEGER, role TEXT, PRIMARY KEY(profile,user));
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, profile TEXT, user INTEGER, topic TEXT, title TEXT, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, edited_text TEXT DEFAULT '', version INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS quality_flags(session TEXT,turn INTEGER,code TEXT,reason TEXT,ask_now INTEGER,seconds REAL,PRIMARY KEY(session,turn,code));
                CREATE TABLE IF NOT EXISTS voice_metrics(id INTEGER PRIMARY KEY,session TEXT,kind TEXT,value_ms REAL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, session TEXT, role TEXT, text TEXT, seconds REAL);
                CREATE TABLE IF NOT EXISTS interview_preferences(id INTEGER PRIMARY KEY, profile TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, session TEXT NOT NULL, turn INTEGER NOT NULL, source_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(session,turn,key));
                CREATE UNIQUE INDEX IF NOT EXISTS interview_preferences_active_key ON interview_preferences(profile,key) WHERE status='active';
                CREATE TABLE IF NOT EXISTS interview_preference_turns(session TEXT NOT NULL, turn INTEGER NOT NULL, PRIMARY KEY(session,turn));
                CREATE TABLE IF NOT EXISTS interview_naming_answers(session TEXT PRIMARY KEY, profile TEXT NOT NULL, turn INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS narrator_links(id INTEGER PRIMARY KEY, profile TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, expires REAL NOT NULL, revoked INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, profile TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, profile TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, answer TEXT DEFAULT '', status TEXT DEFAULT 'draft', source_ids TEXT DEFAULT '[]', time_text TEXT DEFAULT '', people TEXT DEFAULT '', place TEXT DEFAULT '', certainty TEXT DEFAULT 'unverified', version INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS item_revisions(item TEXT, version INTEGER, snapshot TEXT, editor INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS processing_jobs(session TEXT PRIMARY KEY, profile TEXT NOT NULL, input_hash TEXT NOT NULL, snapshot TEXT NOT NULL, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, lease_until REAL DEFAULT 0, lease_token TEXT DEFAULT '', next_run REAL DEFAULT 0, progress INTEGER DEFAULT 0, total INTEGER DEFAULT 0, outputs TEXT DEFAULT '[]', error TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT);
                CREATE TABLE IF NOT EXISTS revisions(session TEXT, version INTEGER, text TEXT, editor INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            if 'voice_dialect' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
                db.execute("ALTER TABLE profiles ADD COLUMN voice_dialect TEXT DEFAULT 'sichuan'")
            if 'interviewer_name_asked' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
                db.execute('ALTER TABLE profiles ADD COLUMN interviewer_name_asked INTEGER NOT NULL DEFAULT 0')
            if 'lease_token' not in {r['name'] for r in db.execute('PRAGMA table_info(processing_jobs)')}:
                db.execute("ALTER TABLE processing_jobs ADD COLUMN lease_token TEXT DEFAULT ''")
            columns={r['name'] for r in db.execute('PRAGMA table_info(items)')}
            for name, definition in [('evidence',"TEXT DEFAULT '[]'"),('generated_job',"TEXT DEFAULT ''"),('input_hash',"TEXT DEFAULT ''"),('model',"TEXT DEFAULT ''")]:
                if name not in columns:db.execute(f'ALTER TABLE items ADD COLUMN {name} {definition}')
            # 已有档案在升级时冻结当前背景，之后编辑不会改写这个来源快照。
            db.execute("INSERT INTO sources(id,profile,title,text) SELECT lower(hex(randomblob(16))),p.id,'导入的原始访谈',p.context FROM profiles p WHERE p.context!='' AND NOT EXISTS(SELECT 1 FROM sources s WHERE s.profile=p.id)")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'life.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_profile(self, user, name, context, voice_dialect="sichuan"):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO profiles(id,name,context,owner,voice_dialect) VALUES(?,?,?,?,?)', (ident,name,context,user,voice_dialect))
            db.execute('INSERT INTO members VALUES(?,?,?)', (ident,user,'owner'))
            if context:
                db.execute('INSERT INTO sources(id,profile,title,text) VALUES(?,?,?,?)',(uuid.uuid4().hex,ident,'导入的原始访谈',context))
        return self.profile(ident, user)

    def profile(self, ident, user):
        with self.db() as db:
            row = db.execute('SELECT p.*, m.role FROM profiles p JOIN members m ON p.id=m.profile WHERE p.id=? AND m.user=?', (ident,user)).fetchone()
            return dict(row) if row else None

    def profiles(self, user):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT p.id,p.name,p.created_at,m.role FROM profiles p JOIN members m ON p.id=m.profile WHERE m.user=? ORDER BY p.created_at', (user,))]

    def create_session(self, profile, user, topic):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO sessions(id,profile,user,topic,title,status) VALUES(?,?,?,?,?,?)', (ident,profile,user,topic,topic,'connecting'))
        return self.session(ident)

    def session(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (ident,)).fetchone()
            return dict(row) if row else None

    def add_turn(self, ident, role, text, seconds):
        if text.strip():
            with self.db() as db:
                return db.execute('INSERT INTO turns(session,role,text,seconds) VALUES(?,?,?,?)', (ident,role,text,seconds)).lastrowid

    def finish_session(self, ident, status):
        with self.db() as db:
            db.execute('UPDATE sessions SET status=? WHERE id=?', (status,ident))

    def turns(self, ident):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,role,text,seconds FROM turns WHERE session=? ORDER BY id', (ident,))]

    def record_preferences(self, session_id, turn_id, text, *, naming_target=None):
        """仅记录已经落库的最终 user 原文；重复/撤销后重放不会复活偏好。"""
        from .preferences import extract_preferences
        requested = extract_preferences(text, naming_target=naming_target)
        changed = []
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            source = db.execute('SELECT s.profile,t.text FROM turns t JOIN sessions s ON s.id=t.session WHERE t.id=? AND t.session=? AND t.role=?',
                                (turn_id, session_id, 'user')).fetchone()
            if not source or source['text'] != text:
                return []
            # Only realtime's first final answer to its opening naming question
            # consumes this opportunity. Failed connects/interim ASR do not.
            if naming_target == 'interviewer_name':
                db.execute('UPDATE profiles SET interviewer_name_asked=1 WHERE id=?', (source['profile'],))
                db.execute('INSERT OR IGNORE INTO interview_naming_answers(session,profile,turn) VALUES(?,?,?)',
                           (session_id, source['profile'], turn_id))
            processed = db.execute('INSERT OR IGNORE INTO interview_preference_turns(session,turn) VALUES(?,?)', (session_id, turn_id))
            if processed.rowcount == 0:
                return []
            for key, value in requested.items():
                if db.execute('SELECT 1 FROM interview_preferences WHERE session=? AND turn=? AND key=?',
                              (session_id, turn_id, key)).fetchone():
                    continue
                current = db.execute("SELECT value FROM interview_preferences WHERE profile=? AND key=? AND status='active'",
                                     (source['profile'], key)).fetchone()
                if current and current['value'] == value:
                    continue
                db.execute("UPDATE interview_preferences SET status='superseded',updated_at=CURRENT_TIMESTAMP WHERE profile=? AND key=? AND status='active'",
                           (source['profile'], key))
                result = db.execute('INSERT INTO interview_preferences(profile,key,value,session,turn,source_text) VALUES(?,?,?,?,?,?)',
                                    (source['profile'], key, value, session_id, turn_id, text[:300]))
                changed.append(dict(db.execute('SELECT id,profile,key,value,session,turn,source_text,created_at FROM interview_preferences WHERE id=?',
                                               (result.lastrowid,)).fetchone()))
        return changed

    def preferences(self, profile_id):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,profile,key,value,session,turn,source_text,created_at FROM interview_preferences WHERE profile=? AND status='active' ORDER BY id", (profile_id,))]

    def revoke_preference(self, profile_id, preference_id):
        with self.db() as db:
            result = db.execute("UPDATE interview_preferences SET status='revoked',updated_at=CURRENT_TIMESTAMP WHERE id=? AND profile=? AND status='active'",
                                (preference_id, profile_id))
            return result.rowcount == 1

    def provider(self):
        path = self.root / 'provider.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        for field, key in [('app_id','LIFE_VOLC_APP_ID'),('access_token','LIFE_VOLC_ACCESS_TOKEN'),('api_key','LIFE_VOLC_API_KEY')]:
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
        expires = time.time()+180*86400
        with self.db() as db:
            result = db.execute('INSERT INTO narrator_links(profile,token_hash,expires) VALUES(?,?,?)', (profile,digest,expires))
            return {'id':result.lastrowid,'token':token,'expires':expires}

    def link(self, token=None, link_id=None):
        with self.db() as db:
            if token:
                row = db.execute('SELECT * FROM narrator_links WHERE token_hash=? AND revoked=0 AND expires>?', (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            else:
                row = db.execute('SELECT * FROM narrator_links WHERE id=? AND revoked=0 AND expires>?', (link_id,time.time())).fetchone()
            if not row:
                return None
            profile = db.execute('SELECT * FROM profiles WHERE id=?',(row['profile'],)).fetchone()
            return {**dict(row),'profile_data':dict(profile)} if profile else None

    def item(self, ident):
        with self.db() as db:
            row=db.execute('SELECT * FROM items WHERE id=?',(ident,)).fetchone()
            if not row:return None
            result=dict(row);result['source_ids']=json.loads(result['source_ids']);result['evidence']=json.loads(result.get('evidence') or '[]');return result

    def add_item(self, profile, data):
        ident=uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO items(id,profile,kind,title,body,answer,status,source_ids,time_text,people,place,certainty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident,profile,data['kind'],data['title'],data['body'],data.get('answer',''),data.get('status','draft'),json.dumps(data.get('source_ids',[])),data.get('time_text',''),data.get('people',''),data.get('place',''),data.get('certainty','unverified')))
        return self.item(ident)

    def suggest_questions(self, session_id):
        import re
        session=self.session(session_id)
        if not session:return
        for turn in self.turns(session_id):
            text=turn['text']
            if turn['role']=='user' and re.search(r'不确定|记不清|不知道.*字|[（(]音[）)]|听不清',text):
                self.add_item(session['profile'],{'kind':'question','title':'这段回忆有待确认','body':text[:2000],'status':'open','source_ids':[session_id]})

    def record_quality(self,session,turn,text,seconds):
        from datetime import datetime,timezone
        from .quality import inspect_transcript
        flags=inspect_transcript(text,datetime.now(timezone.utc).year)
        with self.db() as db:
            for f in flags:db.execute('INSERT OR IGNORE INTO quality_flags VALUES(?,?,?,?,?,?)',(session,turn,f['code'],f['reason'],int(f['ask_now']),seconds))
        return flags

    def metric(self,session,kind,value_ms):
        if kind not in ('connect_ms','response_first_audio_ms') or not 0<=value_ms<=120000:return
        with self.db() as db:db.execute('INSERT INTO voice_metrics(session,kind,value_ms) VALUES(?,?,?)',(session,kind,value_ms))
