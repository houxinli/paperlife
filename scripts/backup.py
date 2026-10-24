"""创建私有离线备份；备份介质仍需由部署方加密并做恢复演练。"""
import argparse,os,shutil,sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--writes-stopped',action='store_true');a=p.parse_args()
if not a.writes_stopped:raise SystemExit('先停止写入，再使用 --writes-stopped；不要把活跃WAV复制冒充一致快照。')
if a.destination.exists():raise SystemExit('目标已存在，拒绝覆盖')
os.umask(0o077);a.destination.mkdir(parents=True,mode=0o700)
for src in a.data.rglob('*'):
 if src.is_symlink():raise SystemExit('拒绝符号链接')
 if not src.is_file() or src.name.endswith(('-wal','-shm')):continue
 dst=a.destination/src.relative_to(a.data);dst.parent.mkdir(parents=True,exist_ok=True)
 if src.suffix=='.sqlite3':
  with sqlite3.connect(src) as reader,sqlite3.connect(dst) as writer:reader.backup(writer)
 else:shutil.copyfile(src,dst)
print('私有备份已创建；请验证恢复并加密保存，切勿提交到仓库。')
