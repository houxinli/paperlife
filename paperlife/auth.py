"""独立身份库；登录不隐含任何家人档案权限。"""

import hashlib, hmac, os, secrets, sqlite3, time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel, Field
from .security import hash_password, verify_password
from .private_route import PrivateRoute


@dataclass
class User:
    id: int
    username: str
    role: str
    display_name: str


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class WechatCode(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class Phone(BaseModel):
    phone: str = Field(pattern=r'^\+861[3-9][0-9]{9}$')


class Otp(Phone):
    code: str = Field(pattern=r'^\d{6}$')


class Auth:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT,display_name TEXT,is_active INTEGER DEFAULT 1);
   CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,user INTEGER,expires REAL);
   CREATE TABLE IF NOT EXISTS identities(provider TEXT,subject TEXT,user INTEGER,PRIMARY KEY(provider,subject));
   CREATE TABLE IF NOT EXISTS attempts(kind TEXT,subject TEXT,ip TEXT,created REAL);
   CREATE TABLE IF NOT EXISTS otp(phone TEXT PRIMARY KEY,digest TEXT,expires REAL,tries INTEGER DEFAULT 0);
   ''')
        key_path = self.path.parent / 'auth.key'
        if not key_path.exists():
            try:
                fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
        self.key = key_path.read_bytes()

    def db(self):
        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

        return connection()

    def create_user(self, name, password, role='user'):
        if len(password) < 12:
            raise ValueError('Password must have at least 12 characters')
        with self.db() as db:
            r = db.execute(
                'INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)',
                (name, hash_password(password), role, name),
            )
            return r.lastrowid

    def record_attempt(self, kind, subject, ip, limit, window, ip_limit=None):
        # 同一账号/号码与同一 IP 分别限流；家庭共用出口 IP 时，IP 上限放宽，避免互相锁死。
        ip_limit = ip_limit or limit * 5
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE created<?', (now - 86400,))
            by_subject, by_ip = db.execute(
                'SELECT count(*) FILTER (WHERE subject=?), count(*) FILTER (WHERE ip=?) FROM attempts WHERE kind=? AND created>?',
                (subject, ip, kind, now - window),
            ).fetchone()
            if by_subject >= limit or by_ip >= ip_limit:
                raise HTTPException(429, '操作太频繁，请稍后再试')
            db.execute('INSERT INTO attempts VALUES(?,?,?,?)', (kind, subject, ip, now))

    def issue(self, uid):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute(
                'INSERT INTO auth_sessions VALUES(?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), uid, time.time() + 30 * 86400),
            )
        return token

    def user(self, token):
        with self.db() as db:
            row = db.execute(
                'SELECT u.id,u.username,u.role,u.display_name FROM users u JOIN auth_sessions s ON s.user=u.id WHERE s.token_hash=? AND s.expires>? AND u.is_active=1',
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
            return User(**dict(row)) if row else None

    def identity(self, provider, subject):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute('SELECT user FROM identities WHERE provider=? AND subject=?', (provider, subject)).fetchone()
            if r:
                if not db.execute('SELECT 1 FROM users WHERE id=? AND is_active=1', (r['user'],)).fetchone():
                    raise HTTPException(403, '账号已停用')
                return r['user']
            name = provider.split(':')[0] + '-' + secrets.token_hex(8)
            uid = db.execute(
                'INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,?,?)',
                (name, '', 'user', '新用户'),
            ).lastrowid
            db.execute('INSERT INTO identities VALUES(?,?,?)', (provider, subject, uid))
            return uid

    def digest(self, phone, code):
        return hmac.new(self.key, (phone + ':' + code).encode(), hashlib.sha256).hexdigest()

    def make_otp(self, phone, ip):
        self.record_attempt('sms', phone, ip, 5, 3600)
        code = f'{secrets.randbelow(1000000):06d}'
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute('SELECT expires FROM otp WHERE phone=?', (phone,)).fetchone()
            if r and r['expires'] > time.time() + 240:
                raise HTTPException(429, '请一分钟后再发送')
            db.execute(
                'INSERT OR REPLACE INTO otp VALUES(?,?,?,0)', (phone, self.digest(phone, code), time.time() + 300)
            )
        return code

    def consume_otp(self, phone, code):
        ok = False
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM otp WHERE phone=?', (phone,)).fetchone()
            if row and row['expires'] > time.time() and row['tries'] < 5:
                ok = hmac.compare_digest(row['digest'], self.digest(phone, code))
                if ok:
                    db.execute('DELETE FROM otp WHERE phone=?', (phone,))
                else:
                    db.execute('UPDATE otp SET tries=tries+1 WHERE phone=?', (phone,))
        return ok

    def authorized(self, uid, digest=None):
        with self.db() as db:
            if digest is None:
                return bool(db.execute('SELECT 1 FROM users WHERE id=? AND is_active=1', (uid,)).fetchone())
            return bool(
                db.execute(
                    'SELECT 1 FROM users u JOIN auth_sessions s ON s.user=u.id WHERE u.id=? AND u.is_active=1 AND s.token_hash=? AND s.expires>?',
                    (uid, digest, time.time()),
                ).fetchone()
            )

    def router(self):
        router = APIRouter(prefix='/auth', route_class=PrivateRoute)

        def current(request: Request):
            header = request.headers.get('authorization', '')
            token = header[7:] if header.startswith('Bearer ') else request.cookies.get('paperlife_session', '')
            user = self.user(token)
            if not user:
                raise HTTPException(401, '请先登录')
            return user

        self.current = current

        def finish(uid, response, request):
            token = self.issue(uid)
            user = self.user(token)
            response.set_cookie(
                'paperlife_session',
                token,
                httponly=True,
                secure=os.getenv('COOKIE_SECURE', 'true') == 'true',
                samesite='lax',
                max_age=30 * 86400,
                path=(request.scope.get('root_path', '') or '') + '/',
            )
            return {'user': user, 'session_id': token}

        @router.get('/providers')
        def providers():
            from .identity_providers import sms_ready

            return {'sms': sms_ready(), 'wechat': bool(os.getenv('WECHAT_APP_ID') and os.getenv('WECHAT_APP_SECRET'))}

        @router.post('/login')
        def login(data: Credentials, request: Request, response: Response):
            self.record_attempt('password', data.username, request.client.host, 10, 300)
            with self.db() as db:
                row = db.execute('SELECT * FROM users WHERE username=? AND is_active=1', (data.username,)).fetchone()
            if not row or not verify_password(data.password, row['password_hash']):
                raise HTTPException(401, '账号或密码不正确')
            return finish(row['id'], response, request)

        @router.get('/me')
        def me(user=Depends(current)):
            return user

        @router.post('/logout')
        def logout(request: Request, response: Response):
            header = request.headers.get('authorization', '')
            token = header[7:] if header.startswith('Bearer ') else request.cookies.get('paperlife_session', '')
            with self.db() as db:
                db.execute(
                    'DELETE FROM auth_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)
                )
            response.delete_cookie('paperlife_session', path=(request.scope.get('root_path', '') or '') + '/')
            return {'ok': True}

        @router.post('/wechat')
        async def wechat(data: WechatCode, request: Request, response: Response):
            from .identity_providers import exchange_wechat

            self.record_attempt('wechat', 'ip:' + request.client.host, request.client.host, 30, 300)
            appid, openid = await exchange_wechat(data.code)
            return finish(self.identity('wechat:' + appid, openid), response, request)

        @router.post('/sms/send')
        async def sms_send(data: Phone, request: Request):
            from .identity_providers import sms_ready, send_sms

            if not sms_ready():
                raise HTTPException(503, '短信登录尚未开通')
            code = self.make_otp(data.phone, request.client.host)
            try:
                await send_sms(data.phone, code)
            except Exception:
                with self.db() as db:
                    db.execute(
                        'DELETE FROM otp WHERE phone=? AND digest=?', (data.phone, self.digest(data.phone, code))
                    )
                raise HTTPException(502, '短信暂时无法发送，请稍后重试')
            return {'sent': True, 'expires_in': 300}

        @router.post('/sms/login')
        def sms_login(data: Otp, request: Request, response: Response):
            self.record_attempt('otp-login', data.phone, request.client.host, 20, 300)
            if not self.consume_otp(data.phone, data.code):
                raise HTTPException(401, '验证码不正确或已过期')
            return finish(self.identity('phone', data.phone), response, request)

        return router
