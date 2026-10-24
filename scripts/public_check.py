"""提交前检查：不读取ignored运行时数据，不输出疑似密钥的值。"""
import re,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
try:files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root,text=True).splitlines()
except subprocess.CalledProcessError:files=[str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and not any(x in p.parts for x in ['data','.venv','.git','__pycache__'])]
errors=[]
for name in set(files):
 p=root/name
 if p.suffix in ('.wav','.pcm','.sqlite3','.db','.pem','.key') or name=='provider.json':errors.append(name+': private artifact');continue
 if not p.is_file():continue
 try:text=p.read_text()
 except UnicodeDecodeError:errors.append(name+': unexpected binary');continue
 if re.search(r'invite=[A-Za-z0-9_-]{32,}',text):errors.append(name+': capability URL')
 if re.search(r'(?i)(?:api_key|access_token|app_secret)\s*[:=]\s*[\'"][A-Za-z0-9_-]{24,}[\'"]',text):errors.append(name+': literal credential')
 if re.search(r'(?:-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)',text):errors.append(name+': private key')
if errors:print('\n'.join(errors));sys.exit(1)
print('Public source checks passed (heuristic; manual review still required).')
