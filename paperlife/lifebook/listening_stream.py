"""静默PCM录音与ASR代理；旁听内容不触发偏好、退出推断或TTS。"""
import asyncio
import json
import time
import wave
from contextlib import suppress
from .listening_asr import connect_asr
from .listening_store import detail, normalized_segments, save_recognition

MAX_SECONDS = 1200
FINAL_TIMEOUT = 8


async def listen(client, store, session, profile, authorize=None):
    sid=session['id']; started=time.monotonic(); audio_bytes=0
    latest=[]; terminal=False; requested=False; failed=False; tasks=[]
    recognition_status='failed'; status='interrupted'
    queue=asyncio.Queue(maxsize=32)

    def allowed():
        if authorize is not None: return bool(authorize())
        p=store.profile(profile['id'],session['user'])
        return bool(p and p['role']!='reader')

    async def emit(event):
        with suppress(Exception): await client.send_json(event)

    async def ending(reason):
        await emit({'type':'ending','session':sid,'reason':reason})

    try:
        if not allowed(): raise ValueError('Access revoked')
        original=detail(store,sid)
        if original['recognition_status']!='pending' or original['session']['version']:
            raise ValueError('Recognition already started')
        # 在供应商连接前建立录音并接收已到达的数据；连接失败也不会删除原声。
        with wave.open(str(store.audio_path(sid)), 'wb') as mic:
            mic.setparams((1,2,16000,0,'NONE','not compressed'))

            async def receive_client():
                nonlocal audio_bytes,requested
                while True:
                    message=await client.receive()
                    if message['type']=='websocket.disconnect': return
                    if not allowed(): raise ValueError('Access revoked')
                    if message.get('bytes') is not None:
                        data=message['bytes']
                        if len(data)>6400 or len(data)%2: raise ValueError('Invalid PCM')
                        new_total=audio_bytes+len(data)
                        if new_total>MAX_SECONDS*32000 or new_total>(time.monotonic()-started+5)*32000:
                            raise ValueError('Audio rate or duration exceeded')
                        mic.writeframes(data);audio_bytes=new_total
                        # 写入先于发送；供应商慢/失败时已收到的完整混合音轨仍在。
                        queue.put_nowait(data)
                    elif message.get('text'):
                        if len(message['text'])>1000: raise ValueError('Control too large')
                        control=json.loads(message['text'])
                        if control.get('type')=='stop':
                            requested=True
                            await ending('button')
                            return

            receiver=asyncio.create_task(receive_client()); tasks.append(receiver)
            async with connect_asr(store) as upstream:
                if not allowed(): raise ValueError('Access revoked')
                store.finish_session(sid,'active')
                await client.send_json({'type':'ready','session':sid,'mode':'listening'})

                async def send_audio():
                    while True:
                        pcm=await queue.get()
                        try:
                            if pcm is None: return
                            await upstream.send_audio(pcm)
                        finally: queue.task_done()

                async def receive_provider():
                    nonlocal latest,terminal
                    async for event in upstream.events():
                        if not allowed(): raise ValueError('Access revoked')
                        latest=normalized_segments(event['segments'])
                        save_recognition(store,sid,latest,'partial')
                        terminal=bool(event.get('final'))
                        await emit({'type':'listening_transcript','segments':latest,'final':terminal})
                        if terminal: return
                    if not terminal: raise ValueError('Incomplete recognition')

                async def guard():
                    nonlocal requested
                    while True:
                        await asyncio.sleep(1)
                        if not allowed(): raise ValueError('Access revoked')
                        if time.monotonic()-started>=MAX_SECONDS:
                            requested=True
                            await ending('limit')
                            return
                        await emit({'type':'saved','seconds':round(audio_bytes/32000,1)})

                sender=asyncio.create_task(send_audio()); provider=asyncio.create_task(receive_provider()); watcher=asyncio.create_task(guard())
                tasks.extend((sender,provider,watcher))
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in done: task.result()
                # Stop/disconnect/limit finishes queued PCM first, then sends the final package.
                # The provider terminal snapshot must settle before persistence/ended.
                if not provider.done():
                    receiver.cancel(); watcher.cancel()
                    await asyncio.gather(receiver,watcher,return_exceptions=True)
                    await asyncio.wait_for(queue.join(),FINAL_TIMEOUT)
                    await upstream.send_audio(b'',final=True)
                    await asyncio.wait_for(asyncio.shield(provider),FINAL_TIMEOUT)
                if not terminal: raise ValueError('Final recognition missing')
                status='ended' if requested else 'interrupted'
    except asyncio.CancelledError:
        failed=True
        raise
    except Exception:
        failed=True
        for task in tasks: task.cancel()
        await emit({'type':'error','message':'旁听识别连接失败或中断；已接收的录音与临时识别已保留。请检查服务配置后重试。'})
    finally:
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        recognition_status='complete' if terminal and not failed else ('incomplete' if latest or audio_bytes else 'failed')
        with suppress(Exception): save_recognition(store,sid,latest,recognition_status)
        store.finish_session(sid,status)
        from .editor_jobs import enqueue_session
        enqueue_session(store,sid)
        await emit({'type':'saved','session':sid,'seconds':round(audio_bytes/32000,1),'recognition_status':recognition_status})
        await emit({'type':'ended','session':sid,'status':status,'recognition_status':recognition_status})
