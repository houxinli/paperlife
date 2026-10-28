"""人生档案库的编号迁移；版本号记录在 SQLite `PRAGMA user_version`。

只追加新迁移，不修改已发布的迁移。每个迁移在升级时只执行一次，
数据回填也放在迁移里，避免每次启动重复执行。
"""


def _columns(db, table):
    return {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}


def _baseline(db):
    # 版本0的库可能来自任意旧版本，所以基线保持幂等：缺什么补什么。
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
        CREATE TABLE IF NOT EXISTS material_revisions(source TEXT NOT NULL,version INTEGER NOT NULL,text TEXT NOT NULL,editor INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(source,version));
        CREATE TABLE IF NOT EXISTS chapters(id TEXT PRIMARY KEY,profile TEXT NOT NULL,title TEXT NOT NULL,brief TEXT NOT NULL DEFAULT '',published_version_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS chapter_versions(id TEXT PRIMARY KEY,chapter TEXT NOT NULL,number INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'draft',edit_version INTEGER NOT NULL DEFAULT 0,title TEXT NOT NULL,body TEXT NOT NULL,paragraphs TEXT NOT NULL,evidence TEXT NOT NULL,sources TEXT NOT NULL,source_refs TEXT NOT NULL,source_hash TEXT NOT NULL,metadata TEXT NOT NULL,base_published_id TEXT,job TEXT UNIQUE,confirmed_by INTEGER,confirmed_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(chapter,number));
        CREATE TABLE IF NOT EXISTS writing_jobs(id TEXT PRIMARY KEY,profile TEXT NOT NULL,chapter TEXT,kind TEXT NOT NULL,requester INTEGER,request_key TEXT UNIQUE NOT NULL,snapshot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',stage TEXT NOT NULL DEFAULT 'pending',checkpoint TEXT NOT NULL DEFAULT '{}',attempts INTEGER NOT NULL DEFAULT 0,next_run REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',version_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
        CREATE INDEX IF NOT EXISTS writing_jobs_pending ON writing_jobs(status,next_run);
        CREATE INDEX IF NOT EXISTS chapters_profile ON chapters(profile);
        CREATE INDEX IF NOT EXISTS chapter_versions_chapter ON chapter_versions(chapter,number);
        CREATE TABLE IF NOT EXISTS listening_originals(session TEXT PRIMARY KEY, segments TEXT NOT NULL DEFAULT '[]', recognition_status TEXT NOT NULL DEFAULT 'pending', finalized INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS listening_annotations(session TEXT NOT NULL, version INTEGER NOT NULL, participants TEXT NOT NULL, bindings TEXT NOT NULL, overrides TEXT NOT NULL, editor INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(session,version));
    ''')
    added = [
        ('profiles', 'voice_dialect', "TEXT DEFAULT 'sichuan'"),
        ('profiles', 'interviewer_name_asked', 'INTEGER NOT NULL DEFAULT 0'),
        ('processing_jobs', 'lease_token', "TEXT DEFAULT ''"),
        ('items', 'evidence', "TEXT DEFAULT '[]'"),
        ('items', 'generated_job', "TEXT DEFAULT ''"),
        ('items', 'input_hash', "TEXT DEFAULT ''"),
        ('items', 'model', "TEXT DEFAULT ''"),
        ('items', 'generation_metadata', "TEXT DEFAULT '{}'"),
        ('items', 'source_snapshot', "TEXT DEFAULT '[]'"),
        ('sessions', 'mode', "TEXT NOT NULL DEFAULT 'dialogue'"),
    ]
    for table, name, definition in added:
        if name not in _columns(db, table):
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    # 已有档案在升级时冻结当前背景，之后编辑不会改写这个来源快照。
    db.execute(
        "INSERT INTO sources(id,profile,title,text) SELECT lower(hex(randomblob(16))),p.id,'导入的原始访谈',p.context FROM profiles p WHERE p.context!='' AND NOT EXISTS(SELECT 1 FROM sources s WHERE s.profile=p.id)"
    )


def _retire_legacy_queue(db):
    # 旧整理队列已移除：已完成任务的草稿保留为历史成果；未完成任务交给新队列重新整理。
    db.execute(
        "UPDATE processing_jobs SET status='superseded',lease_until=0,error='已由新整理队列接管' WHERE status!='completed'"
    )


def _indexes_and_job_session(db):
    # 常用外键列补索引；素材任务的来源会话单独成列，避免后台每轮解析整份快照 JSON。
    # executescript 会先提交，所以加列保持幂等，中途失败后可以安全重跑。
    if 'source_session' not in _columns(db, 'writing_jobs'):
        db.execute('ALTER TABLE writing_jobs ADD COLUMN source_session TEXT')
    db.execute(
        "UPDATE writing_jobs SET source_session=json_extract(snapshot,'$.sources[0].id') WHERE kind='material' AND json_extract(snapshot,'$.sources[0].kind')='session'"
    )
    db.executescript('''
        CREATE INDEX IF NOT EXISTS turns_session ON turns(session,id);
        CREATE INDEX IF NOT EXISTS sessions_profile ON sessions(profile,created_at);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user,created_at);
        CREATE INDEX IF NOT EXISTS members_user ON members(user);
        CREATE INDEX IF NOT EXISTS items_profile ON items(profile,created_at);
        CREATE INDEX IF NOT EXISTS narrator_links_profile ON narrator_links(profile);
        CREATE INDEX IF NOT EXISTS writing_jobs_profile ON writing_jobs(profile,created_at);
        CREATE INDEX IF NOT EXISTS writing_jobs_source_session ON writing_jobs(source_session) WHERE source_session IS NOT NULL;
    ''')


MIGRATIONS = [_baseline, _retire_legacy_queue, _indexes_and_job_session]


def version(db):
    return db.execute('PRAGMA user_version').fetchone()[0]


def migrate(db):
    current = version(db)
    if current > len(MIGRATIONS):
        raise RuntimeError(f'数据库版本 {current} 高于程序支持的 {len(MIGRATIONS)}，请先升级程序')
    for number, step in enumerate(MIGRATIONS[current:], current + 1):
        step(db)
        db.execute(f'PRAGMA user_version={number}')
        db.commit()
