"""Paperlife 独立服务，不加载任何交易模块。"""
import asyncio,os
from contextlib import asynccontextmanager,suppress
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from .auth import Auth
from .lifebook.api import build_router
from .lifebook.store import Store
from .lifebook.processing import worker,recover_interrupted

def create_app(data_dir=None,start_worker=True):
 root=Path(data_dir or os.getenv('PAPERLIFE_DATA_DIR','data'));root.mkdir(parents=True,exist_ok=True,mode=0o700)
 auth=Auth(root/'auth.sqlite3');auth_router=auth.router();store=Store(root/'life')
 password_file=os.getenv('PAPERLIFE_BOOTSTRAP_PASSWORD_FILE')
 if password_file:
  with auth.db() as db:empty=db.execute('SELECT count(*) FROM users').fetchone()[0]==0
  if empty:auth.create_user(os.getenv('PAPERLIFE_ADMIN_USERNAME','admin'),Path(password_file).read_text().strip(),'admin')
 @asynccontextmanager
 async def lifespan(app):
  task=None
  if start_worker:
   recover_interrupted(store);task=asyncio.create_task(worker(store))
  yield
  if task:
   task.cancel()
   with suppress(asyncio.CancelledError):await task
 app=FastAPI(title='Paperlife',lifespan=lifespan,root_path=os.getenv('PAPERLIFE_ROOT_PATH',''))
 app.state.auth=auth;app.state.store=store
 app.include_router(auth_router,prefix='/api');app.include_router(build_router(root/'auth.sqlite3',auth.current,account_authorized=auth.authorized),prefix='/api')
 @app.get('/health')
 def health():return {'status':'ok','service':'paperlife'}
 @app.get('/')
 def index():return RedirectResponse((os.getenv('PAPERLIFE_ROOT_PATH',''))+'/life/')
 app.mount('/life',StaticFiles(directory=Path(__file__).resolve().parents[1]/'web/life',html=True),name='life')
 return app

app=create_app()
