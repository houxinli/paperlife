"""本机/容器创建管理员，不在命令行参数中接收密码。"""
import argparse,getpass,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperlife.auth import Auth
p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'));p.add_argument('--username',default='admin');a=p.parse_args()
password=getpass.getpass('新管理员密码（至少12字符）：')
if password!=getpass.getpass('再次输入：'):raise SystemExit('两次密码不一致')
Auth(a.data/'auth.sqlite3').create_user(a.username,password,'admin');print('管理员已创建')
