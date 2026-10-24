"""实时音频代理；持久化与授权由应用控制，不把密钥发给浏览器。"""
import asyncio
import json
import logging
import time
import uuid
import wave
from contextlib import suppress

from websockets.asyncio.client import connect

from .prompt import session_config, opening_instruction
from .protocol import decode, encode

URL = 'wss://openspeech.bytedance.com/api/v3/realtime/dialogue'
logger = logging.getLogger(__name__)


async def interview(client, store, session, profile, authorize=None):
    sid = session['id']
    config = store.provider()
    headers = {'X-Api-Resource-Id':'volc.speech.dialog', 'X-Api-Connect-Id':str(uuid.uuid4())}
    if config.get('api_key'):
        headers['X-Api-Key'] = config['api_key']
    else:
        headers.update({'X-Api-App-ID':config['app_id'], 'X-Api-Access-Key':config['access_token'], 'X-Api-App-Key':'PlgvMymc7f3tQnJ6'})
    status = 'interrupted'
    started = time.monotonic()
    pending = {'user':'','assistant':''}
    turn_start = {'user':0.0,'assistant':0.0}
    user_bytes = 0
    diagnostics = {'provider_events':{}, 'audio_chunks':0, 'audio_bytes':0, 'forwarded_audio_chunks':0}
    def save(role):
        changes=[]
        if pending[role]:
            turn_id=store.add_turn(sid,role,pending[role],turn_start[role])
            if role=='user':
                store.record_quality(sid,turn_id,pending[role],turn_start[role])
                changes=store.record_preferences(sid,turn_id,pending[role])
            pending[role] = ''
        return changes
    with store.db() as db:
        previous = db.execute('SELECT id,edited_text FROM sessions WHERE profile=? AND id!=? ORDER BY created_at DESC LIMIT 1', (profile['id'],sid)).fetchone()
    with store.db() as db:
        resolved=[dict(r) for r in db.execute("SELECT title,answer FROM items WHERE profile=? AND kind='question' AND status='resolved' AND answer!='' ORDER BY created_at DESC LIMIT 15",(profile['id'],))]
    profile={**profile,'_interview_preferences':store.preferences(profile['id']),'context':profile['context']+'\n家人已回答的问题（补充来源，仍可向讲述者核对）：\n'+'\n'.join(r['title']+'：'+r['answer'] for r in resolved)}
    recent = ''
    if previous:
        recent = previous['edited_text'] or '\n'.join(t['role']+': '+t['text'] for t in store.turns(previous['id']))[-3000:]
    try:
        async with connect(URL,additional_headers=headers,open_timeout=15,max_size=2*1024*1024,ping_interval=20) as upstream:
            await upstream.send(encode(1,{}))
            event = decode(await asyncio.wait_for(upstream.recv(),15))
            if event['event'] != 50:
                raise ValueError('Connection rejected')
            await upstream.send(encode(100,session_config(profile,session['topic'],recent),sid))
            event = decode(await asyncio.wait_for(upstream.recv(),20))
            if event['event'] != 150:
                raise ValueError('Session rejected')
            store.metric(sid,'connect_ms',(time.monotonic()-started)*1000)
            store.finish_session(sid,'active')
            with wave.open(str(store.audio_path(sid)), 'wb') as mic, wave.open(str(store.audio_path(sid,'assistant')), 'wb') as voice:
                mic.setparams((1,2,16000,0,'NONE','not compressed'))
                voice.setparams((1,2,24000,0,'NONE','not compressed'))
                await client.send_json({'type':'ready','session':sid})
                await upstream.send(encode(501,{'content':opening_instruction(profile,recent)},sid))

                async def receive_client():
                    nonlocal user_bytes,status
                    while True:
                        message = await client.receive()
                        if message['type']=='websocket.disconnect':
                            return
                        if message.get('bytes') is not None:
                            data = message['bytes']
                            if len(data)>6400 or len(data)%2:
                                raise ValueError('Invalid PCM')
                            user_bytes += len(data)
                            # 限制非实时灌流和每次采访的资源使用。
                            if user_bytes > (time.monotonic()-started+5)*32000:
                                raise ValueError('Audio rate exceeded')
                            mic.writeframes(data)
                            await upstream.send(encode(200,data,sid))
                        elif message.get('text'):
                            if len(message['text'])>1000:
                                raise ValueError('Control too large')
                            control = json.loads(message['text'])
                            if control.get('type')=='stop':
                                status='ended'
                                # 留出尾句端点检测时间，接收器继续收最后的转写。
                                await asyncio.sleep(2.7)
                                return

                async def receive_provider():
                    last_reply = None
                    input_finished=None
                    async for raw in upstream:
                        response = decode(raw)
                        event,data = response['event'],response['data']
                        counts=diagnostics['provider_events']
                        counts[str(event)]=counts.get(str(event),0)+1
                        elapsed = user_bytes/32000
                        if event in (51,153,599):
                            raise ValueError('Provider session failure')
                        if event == 450:
                            save('assistant')
                            turn_start['user'] = elapsed
                            await client.send_json({'type':'interrupt','question_id':data.get('question_id')})
                        elif event == 451:
                            for result in data.get('results',[]):
                                pending['user'] = result.get('text','')
                                await client.send_json({'type':'transcript','role':'user','text':pending['user'],'final':not result.get('is_interim',True)})
                                if not result.get('is_interim',True):
                                    if save('user'):
                                        await client.send_json({'type':'preference_saved'})
                        elif event == 459:
                            if save('user'):
                                await client.send_json({'type':'preference_saved'})
                            input_finished=time.monotonic()
                        elif event in (350,351,359):
                            # 音频和文本为独立流；用 TTS 边界控制播放，不依赖 550 到达顺序。
                            await client.send_json({'type':{350:'audio_start',351:'audio_sentence_end',359:'audio_end'}[event],
                                                    'reply_id':data.get('reply_id'),'question_id':data.get('question_id')})
                        elif event == 550:
                            reply = data.get('reply_id')
                            if reply != last_reply or not pending['assistant']:
                                await client.send_json({'type':'answer_start','source':'text','reply_id':reply,'question_id':data.get('question_id')})
                                last_reply = reply
                            text = data.get('content','')
                            if not pending['assistant']:
                                turn_start['assistant']=elapsed
                            pending['assistant'] += text
                            await client.send_json({'type':'transcript','role':'assistant','text':pending['assistant'],'final':False})
                        elif event == 559:
                            await client.send_json({'type':'transcript','role':'assistant','text':pending['assistant'],'final':True})
                            save('assistant')
                        elif event == 352 and isinstance(data,bytes):
                            diagnostics['audio_chunks']+=1
                            diagnostics['audio_bytes']+=len(data)
                            if input_finished is not None:
                                store.metric(sid,'response_first_audio_ms',(time.monotonic()-input_finished)*1000);input_finished=None
                            voice.writeframes(data)
                            await client.send_bytes(data)
                            diagnostics['forwarded_audio_chunks']+=1
                        elif event == 152:
                            return

                async def guard():
                    while True:
                        await asyncio.sleep(1)
                        p = store.profile(profile['id'],session['user']) if authorize is None else None
                        allowed=authorize() if authorize else (p and p['role']!='reader')
                        if not allowed:
                            raise ValueError('Access revoked')
                        if time.monotonic()-started>1200:
                            await client.send_json({'type':'notice','message':'本次已聊满20分钟，已结束；可以稍后继续。'})
                            return
                        await client.send_json({'type':'saved','seconds':round(user_bytes/32000,1)})

                tasks = [asyncio.create_task(fn()) for fn in (receive_client,receive_provider,guard)]
                try:
                    done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if status == 'ended':
                        await upstream.send(encode(102,{},sid))
                        with suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(asyncio.shield(tasks[1]),3)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    with suppress(Exception):
                        if status != 'ended':
                            await upstream.send(encode(102,{},sid))
                        await upstream.send(encode(2,{}))
    except Exception as error:
        diagnostics['failure_type']=type(error).__name__
        await client.send_json({'type':'error','message':'实时语音连接失败或中断。请检查豆包服务是否开通、凭据及余额；已接收的录音保留在历史采访中。'})
    finally:
        # 仅记录协议计数和错误类别，不记录凭据、素材、转写或供应商错误原文。
        logger.info('voice_transport %s',json.dumps(diagnostics,sort_keys=True))
        save('user')
        save('assistant')
        store.finish_session(sid,status)
        from .processing import enqueue
        enqueue(store,sid)
        with suppress(Exception):
            await client.send_json({'type':'voice_diagnostic',**diagnostics})
            await client.send_json({'type':'ended','session':sid,'status':status})
