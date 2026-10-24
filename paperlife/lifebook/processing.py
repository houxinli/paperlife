"""持久化、幂等的访谈后整理队列。只有校验过来源的草稿能写入。"""
import asyncio
import hashlib
import json
import time
import uuid
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
from .generation import generate,MODEL

class Citation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    turn_id:int=Field(gt=0)
    quote:str=Field(min_length=1,max_length=500)

class Draft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['person','event','fact','question']
    title:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1,max_length=4000)
    time_text:str=Field(default='',max_length=200)
    people:str=Field(default='',max_length=500)
    place:str=Field(default='',max_length=300)
    citations:list[Citation]=Field(min_length=1,max_length=10)

class Extraction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items:list[Draft]=Field(max_length=24)

class Paragraph(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=1,max_length=2000)
    citations:list[Citation]=Field(min_length=1,max_length=10)

class Story(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
    paragraphs:list[Paragraph]=Field(min_length=1,max_length=5)

class Stories(BaseModel):
    model_config=ConfigDict(extra='forbid')
    stories:list[Story]=Field(max_length=2)


def enqueue(store,session_id):
    session=store.session(session_id)
    if not session or session['status'] not in ('ended','interrupted'):return None
    turns=[];context='';context_id=None
    for turn in store.turns(session_id):
        if turn['role']=='assistant':context=turn['text'];context_id=turn['id']
        elif turn['role']=='user' and turn['text'].strip():
            turns.append({**turn,'context':context,'context_turn_id':context_id})
    if not turns:return None
    snapshot=json.dumps(turns,ensure_ascii=False,sort_keys=True)
    digest=hashlib.sha256(snapshot.encode()).hexdigest()
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO processing_jobs(session,profile,input_hash,snapshot) VALUES(?,?,?,?)',(session_id,session['profile'],digest,snapshot))
        return dict(db.execute('SELECT session,status,attempts,progress,total,error FROM processing_jobs WHERE session=?',(session_id,)).fetchone())


def claim(store):
    now=time.time();lease=uuid.uuid4().hex
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE processing_jobs SET status='failed',error='任务执行中断，请手动重试' WHERE status='running' AND lease_until<? AND attempts>=3",(now,))
        row=db.execute("SELECT * FROM processing_jobs WHERE attempts<3 AND ((status='pending' AND next_run<=?) OR (status='running' AND lease_until<?)) ORDER BY created_at LIMIT 1",(now,now)).fetchone()
        if not row:return None
        db.execute("UPDATE processing_jobs SET status='running',attempts=attempts+1,lease_until=?,lease_token=? WHERE session=?",(now+600,lease,row['session']))
        return {**dict(row),'attempts':row['attempts']+1,'lease_token':lease}


def chunks(turns,limit=5000):
    current=[];size=0
    for turn in turns:
        # 长单轮也完整分片，保留相同turn ID及原句来源；不静默截掉尾部。
        for offset in range(0,len(turn['text']),limit-1000):
            fragment={**turn,'text':turn['text'][offset:offset+limit-1000],'context':turn.get('context','')[:600]}
            cost=len(json.dumps(fragment,ensure_ascii=False))
            if current and size+cost>limit:
                yield current;current=[];size=0
            current.append(fragment);size+=cost
    if current:yield current


def checked_citations(citations,turns,session,paragraph=None):
    known={t['id']:t for t in turns}
    evidence=[]
    for c in citations:
        turn=known.get(c.turn_id)
        if not turn or c.quote not in turn['text']:raise ValueError('invalid_source_quote')
        if len(c.quote)<4 and c.quote!=turn['text'].strip():raise ValueError('short_partial_quote')
        evidence.append({'session_id':session,'turn_id':c.turn_id,'quote':c.quote,'seconds':turn['seconds'],'context_turn_id':turn.get('context_turn_id'),**({'paragraph':paragraph} if paragraph is not None else {})})
    return evidence


def validate_output(stage,raw,turns,session):
    output=[]
    if stage=='extract':
        for item in Extraction.model_validate(raw).items:
            output.append({**item.model_dump(exclude={'citations'}),'evidence':checked_citations(item.citations,turns,session)})
    else:
        for story in Stories.model_validate(raw).stories:
            evidence=[]
            for index,p in enumerate(story.paragraphs):evidence.extend(checked_citations(p.citations,turns,session,index+1))
            output.append({'kind':'story','title':story.title,'body':'\n\n'.join(p.text for p in story.paragraphs),'evidence':evidence})
    return output


async def process_one(store,generator=None):
    job=claim(store)
    if not job:return False
    stages=[(stage,chunk) for chunk in chunks(json.loads(job['snapshot'])) for stage in ('extract','story')]
    outputs=json.loads(job['outputs'])
    try:
        with store.db() as db:db.execute('UPDATE processing_jobs SET total=? WHERE session=? AND lease_token=?',(len(stages),job['session'],job['lease_token']))
        for index,(stage,turns) in enumerate(stages):
            if index<job['progress']:continue
            raw=await (generator(stage,turns) if generator else generate(store,stage,turns))
            result=validate_output(stage,raw,turns,job['session'])
            outputs.extend(result)
            with store.db() as db:
                db.execute('UPDATE processing_jobs SET progress=?,outputs=?,lease_until=? WHERE session=? AND lease_token=?',(index+1,json.dumps(outputs,ensure_ascii=False),time.time()+600,job['session'],job['lease_token']))
        # 原子提交所有阶段，完成后重复执行不会重复入书或覆盖人工作品。
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT status,lease_token FROM processing_jobs WHERE session=?',(job['session'],)).fetchone()
            if existing['status']!='running' or existing['lease_token']!=job['lease_token']:return True
            for item in outputs:
                db.execute("INSERT INTO items(id,profile,kind,title,body,status,source_ids,time_text,people,place,evidence,generated_job,input_hash,model) VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?,?)",
                           (uuid.uuid4().hex,job['profile'],item['kind'],item['title'],item['body'],json.dumps([job['session']]),item.get('time_text',''),item.get('people',''),item.get('place',''),json.dumps(item['evidence'],ensure_ascii=False),job['session'],job['input_hash'],MODEL))
            db.execute("UPDATE processing_jobs SET status='completed',error='',lease_until=0,completed_at=CURRENT_TIMESTAMP WHERE session=?",(job['session'],))
    except asyncio.CancelledError:
        with store.db() as db:db.execute("UPDATE processing_jobs SET status='pending',lease_until=0,attempts=attempts-1 WHERE session=? AND lease_token=? AND status='running'",(job['session'],job['lease_token']))
        raise
    except Exception:
        with store.db() as db:
            db.execute("UPDATE processing_jobs SET status=?,next_run=?,lease_until=0,error=? WHERE session=? AND lease_token=? AND status='running'",('failed' if job['attempts']>=3 else 'pending',time.time()+30*job['attempts'],'整理未完成：服务响应或来源校验未通过；原始资料已保留。',job['session'],job['lease_token']))
    return True


async def worker(store):
    while True:
        try:
            enqueue_missing(store)
            await process_one(store)
        except asyncio.CancelledError:raise
        except Exception:pass  # 单个任务失败不能停止后续采集；状态通过jobs接口可见。
        await asyncio.sleep(3)


def enqueue_missing(store):
    with store.db() as db:
        ids=[r['id'] for r in db.execute("SELECT s.id FROM sessions s WHERE s.status IN ('ended','interrupted') AND EXISTS(SELECT 1 FROM turns t WHERE t.session=s.id AND t.role='user' AND trim(t.text)!='') AND NOT EXISTS(SELECT 1 FROM processing_jobs j WHERE j.session=s.id)")]
    for ident in ids:enqueue(store,ident)


def recover_interrupted(store):
    # 当前生产部署为单进程；启动前不存在可继续使用的旧WebSocket/ticket。
    with store.db() as db:
        db.execute("UPDATE sessions SET status='interrupted' WHERE status IN ('active','connecting')")
    enqueue_missing(store)
