"""停写后的私有迁移；默认dry-run，永不复制交易表、登录会话和供应商密钥。"""
import argparse,hashlib,json,os,re,shutil,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperlife.auth import Auth

def migrate(source_life,source_users,destination,apply=False,source_stopped=False):
 source_life=Path(source_life);source_users=Path(source_users);destination=Path(destination)
 if not (source_life/'life.sqlite3').is_file():raise ValueError('Missing life database')
 if destination.exists():raise ValueError('Destination must not exist')
 if source_life.is_symlink() or source_users.is_symlink():raise ValueError('Symlinks are not supported')
 with sqlite3.connect(f'file:{source_life / "life.sqlite3"}?mode=ro',uri=True) as db:
  uids={r[0] for r in db.execute('SELECT owner FROM profiles UNION SELECT user FROM members UNION SELECT user FROM sessions WHERE user>0')}
  profile_count=db.execute('SELECT count(*) FROM profiles').fetchone()[0]
 with sqlite3.connect(f'file:{source_users}?mode=ro',uri=True) as db:
  rows=[db.execute('SELECT id,username,password_hash,role,display_name,is_active FROM users WHERE id=?',(u,)).fetchone() for u in sorted(uids)]
 if any(r is None for r in rows):raise ValueError('Referenced account missing; resolve before migration')
 audio=[]
 for f in source_life.iterdir():
  if f.is_symlink():raise ValueError('Unexpected symlink')
  if re.fullmatch(r'[a-f0-9]{32}-(user|assistant)\.wav',f.name):audio.append(f)
  elif f.name not in ('life.sqlite3','life.sqlite3-wal','life.sqlite3-shm','provider.json'):
   raise ValueError('Unexpected runtime artifact; review before migration')
 summary={'profiles':profile_count,'accounts':len(rows),'audio_files':len(audio),'applied':False}
 if not apply:return summary
 if not source_stopped:raise ValueError('Stop source writes and pass --source-stopped first')
 os.umask(0o077);destination.mkdir(parents=True,mode=0o700);(destination/'life').mkdir(mode=0o700)
 auth=Auth(destination/'auth.sqlite3')
 with auth.db() as db:db.executemany('INSERT INTO users(id,username,password_hash,role,display_name,is_active) VALUES(?,?,?,?,?,?)',rows)
 with sqlite3.connect(f'file:{source_life / "life.sqlite3"}?mode=ro',uri=True) as src,sqlite3.connect(destination/'life/life.sqlite3') as dst:src.backup(dst)
 manifest=[]
 for f in audio:
  target=destination/'life'/f.name;shutil.copyfile(f,target)
  def digest(p):
   with p.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()
  before=digest(f);after=digest(target)
  if before!=after:raise ValueError('Audio integrity mismatch')
  manifest.append({'file':f.name,'sha256':after,'bytes':target.stat().st_size})
 (destination/'migration-manifest.json').write_text(json.dumps({**summary,'applied':True,'audio':manifest},ensure_ascii=False,indent=2))
 return {**summary,'applied':True}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source-life',type=Path,required=True);p.add_argument('--source-users',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--apply',action='store_true');p.add_argument('--source-stopped',action='store_true');a=p.parse_args()
 print(json.dumps(migrate(a.source_life,a.source_users,a.destination,a.apply,a.source_stopped),ensure_ascii=False))
