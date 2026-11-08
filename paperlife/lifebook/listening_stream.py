"""静默PCM录音与ASR代理；旁听内容不触发偏好、退出推断或TTS。"""

import asyncio
import json
import logging
import time
import wave
from contextlib import suppress
from . import listening_sides
from .listening_asr import MAX_DURATION_MS, connect_asr
from .listening_gain import AutoGain
from .listening_notes import live_notes
from .listening_store import detail, normalized_segments, save_recognition

MAX_SECONDS = MAX_DURATION_MS // 1000
FINAL_TIMEOUT = 8
# 识别结果是累计快照，长录音时整份落库代价随时长增长；按间隔保存，结束时总会保存最终结果。
SNAPSHOT_SECONDS = 5
# 上游短暂变慢时缓冲约 30 秒网页音频（每包 20ms），超过才中断。
QUEUE_PACKETS = 1500
CONTROL_LIMIT = 1000
# AI 实时笔记：约每分钟一次，新增识别文字太少就跳过；一次调用最多等这么久。
NOTES_SECONDS = 60
NOTES_MIN_CHARS = 60
NOTES_TIMEOUT = 90
log = logging.getLogger(__name__)


async def listen(client, store, session, profile, authorize=None):
    sid = session['id']
    started = time.monotonic()
    audio_bytes = 0
    latest = []
    terminal = False
    requested = False
    failed = False
    tasks = []
    recognition_status = 'failed'
    status = 'interrupted'
    queue = asyncio.Queue(maxsize=QUEUE_PACKETS)
    snapshot_at = started
    shown = []
    level = AutoGain()
    checked_at = None
    permitted = False
    helpers = []
    notes = {'enabled': False, 'off': False, 'chars': 0, 'known': []}
    # 实时两端归边：通话那头默认是档案主体，旁边是“我”；定稿后按整段原声重算并保存为建议。
    live = listening_sides.Live()
    sides = {}
    side_names = {'far': profile['name'], 'near': '我'}

    def allowed():
        # 撤权最迟一秒生效，与 guard 的节奏一致；不为每个 20ms 音频包查一次库。
        nonlocal checked_at, permitted
        now = time.monotonic()
        if checked_at is None or now - checked_at >= 1:
            if authorize is not None:
                permitted = bool(authorize())
            else:
                p = store.profile(profile['id'], session['user'])
                permitted = bool(p and p['role'] != 'reader')
            checked_at = now
        return permitted

    async def emit(event):
        with suppress(Exception):
            await client.send_json(event)

    async def ending(reason):
        await emit({'type': 'ending', 'session': sid, 'reason': reason})

    def named(segments):
        # 给 AI 笔记的说话人：已归边的句子用名字，拿不准的照样当作声音组。
        return [{**s, 'name': side_names.get(sides.get(s['id'], '').rstrip('?'))} for s in segments]

    async def take_notes():
        # 只给正在通话的家人看的提示，不入档案；任何失败都不影响录音和识别。
        while True:
            await asyncio.sleep(NOTES_SECONDS)
            chars = sum(len(s['text']) for s in latest)
            if not notes['enabled'] or notes['off'] or chars - notes['chars'] < NOTES_MIN_CHARS:
                continue
            notes['chars'] = chars
            try:
                result = await live_notes(store, profile['name'], named(latest), notes['known'], NOTES_TIMEOUT)
            except Exception as error:
                code = str(error) if type(error) is ValueError and str(error).isidentifier() else type(error).__name__
                notes['off'] = code == 'provider_unconfigured'
                # 只记录本模块的固定错误码或异常类型，不记录对话内容。
                log.warning('listening notes failed: %s (session=%s)', code, sid)
                message = '文本模型尚未配置，AI 笔记已关闭。' if notes['off'] else 'AI 笔记这次没有生成，稍后会再试。'
                await emit({'type': 'listening_notes', 'error': message})
                continue
            fresh = [point for point in result['points'] if point['text'] not in notes['known']]
            notes['known'].extend(point['text'] for point in fresh)
            await emit({'type': 'listening_notes', 'points': fresh, 'questions': result['questions']})

    try:
        if not allowed():
            raise ValueError('Access revoked')
        original = detail(store, sid)
        if original['recognition_status'] != 'pending' or original['session']['version']:
            raise ValueError('Recognition already started')
        # 在供应商连接前建立录音并接收已到达的数据；连接失败也不会删除原声。
        with wave.open(str(store.audio_path(sid)), 'wb') as mic:
            mic.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))

            async def receive_client():
                nonlocal audio_bytes, requested
                while True:
                    message = await client.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    if not allowed():
                        raise ValueError('Access revoked')
                    if message.get('bytes') is not None:
                        data = message['bytes']
                        if len(data) > 6400 or len(data) % 2:
                            raise ValueError('Invalid PCM')
                        new_total = audio_bytes + len(data)
                        if new_total > MAX_SECONDS * 32000 or new_total > (time.monotonic() - started + 5) * 32000:
                            raise ValueError('Audio rate or duration exceeded')
                        # 先调到正常音量再保存和识别：外放旁边的原始电平太低，识别会漏字，回听也听不清。
                        data = level.process(data)
                        live.add(data)
                        mic.writeframes(data)
                        audio_bytes = new_total
                        # 写入先于发送；供应商慢/失败时已收到的完整混合音轨仍在。
                        queue.put_nowait(data)
                    elif message.get('text'):
                        if len(message['text']) > CONTROL_LIMIT:
                            raise ValueError('Control too large')
                        control = json.loads(message['text'])
                        if control.get('type') == 'stop':
                            requested = True
                            await ending('button')
                            return
                        if control.get('type') == 'notes':
                            notes['enabled'] = control.get('enabled') is True

            receiver = asyncio.create_task(receive_client())
            tasks.append(receiver)
            async with connect_asr(store) as upstream:
                if not allowed():
                    raise ValueError('Access revoked')
                store.finish_session(sid, 'active')
                await client.send_json({'type': 'ready', 'session': sid, 'mode': 'listening'})

                async def send_audio():
                    while True:
                        pcm = await queue.get()
                        try:
                            if pcm is None:
                                return
                            await upstream.send_audio(pcm)
                        finally:
                            queue.task_done()

                async def receive_provider():
                    nonlocal latest, terminal, snapshot_at, shown, sides
                    async for event in upstream.events():
                        if not allowed():
                            raise ValueError('Access revoked')
                        latest = normalized_segments(event['segments'])
                        terminal = bool(event.get('final'))
                        if not terminal and time.monotonic() - snapshot_at >= SNAPSHOT_SECONDS:
                            save_recognition(store, sid, latest, 'partial')
                            snapshot_at = time.monotonic()
                        # 快照是累计全文，但临时修订只发生在末尾几句：只下发从第一处变化开始的分句，
                        # 网页按 from/total 拼出整场实时记录，长录音也不必每次重发全文。
                        start = next(
                            (i for i, (old, new) in enumerate(zip(shown, latest)) if old != new),
                            min(len(shown), len(latest)),
                        )
                        # 两端归边只下发有变化的句子（分界随录音自适应，早先的句子也可能改边）。
                        current = live.update(latest)
                        changed = {k: v for k, v in current.items() if sides.get(k) != v}
                        sides = current
                        await emit(
                            {
                                'type': 'listening_transcript',
                                'from': start,
                                'segments': latest[start:],
                                'total': len(latest),
                                'final': terminal,
                                'sides': changed,
                                'names': side_names,
                            }
                        )
                        shown = latest
                        if terminal:
                            return
                    if not terminal:
                        raise ValueError('Incomplete recognition')

                async def guard():
                    nonlocal requested
                    while True:
                        await asyncio.sleep(1)
                        if not allowed():
                            raise ValueError('Access revoked')
                        if time.monotonic() - started >= MAX_SECONDS:
                            requested = True
                            await ending('limit')
                            return
                        await emit({'type': 'saved', 'seconds': round(audio_bytes / 32000, 1)})

                sender = asyncio.create_task(send_audio())
                provider = asyncio.create_task(receive_provider())
                watcher = asyncio.create_task(guard())
                tasks.extend((sender, provider, watcher))
                # 笔记不参与“谁先结束”的判断：它不会结束录音，收尾时直接取消。
                helpers.append(asyncio.create_task(take_notes()))
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in helpers:
                    task.cancel()
                for task in done:
                    task.result()
                # Stop/disconnect/limit finishes queued PCM first, then sends the final package.
                # The provider terminal snapshot must settle before persistence/ended.
                if not provider.done():
                    receiver.cancel()
                    watcher.cancel()
                    await asyncio.gather(receiver, watcher, return_exceptions=True)
                    await asyncio.wait_for(queue.join(), FINAL_TIMEOUT)
                    await upstream.send_audio(b'', final=True)
                    await asyncio.wait_for(asyncio.shield(provider), FINAL_TIMEOUT)
                if not terminal:
                    raise ValueError('Final recognition missing')
                status = 'ended' if requested else 'interrupted'
    except asyncio.CancelledError:
        failed = True
        raise
    except Exception:
        failed = True
        for task in tasks:
            task.cancel()
        await emit(
            {'type': 'error', 'message': '旁听识别连接失败或中断；已接收的录音与临时识别已保留。请检查服务配置后重试。'}
        )
    finally:
        for task in tasks + helpers:
            task.cancel()
        if tasks or helpers:
            await asyncio.gather(*tasks, *helpers, return_exceptions=True)
        recognition_status = (
            'complete' if terminal and not failed else ('incomplete' if latest or audio_bytes else 'failed')
        )
        with suppress(Exception):
            save_recognition(store, sid, latest, recognition_status)
        store.finish_session(sid, status)
        if recognition_status in ('complete', 'incomplete'):
            try:
                # 读整段原声做 FFT，放到线程里，不占共享事件循环。
                await asyncio.to_thread(listening_sides.refresh, store, sid, latest)
            except Exception as error:
                log.warning('listening sides not saved: %s (session=%s)', type(error).__name__, sid)
        from .editor_jobs import enqueue_session

        enqueue_session(store, sid)
        await emit(
            {
                'type': 'saved',
                'session': sid,
                'seconds': round(audio_bytes / 32000, 1),
                'recognition_status': recognition_status,
            }
        )
        await emit({'type': 'ended', 'session': sid, 'status': status, 'recognition_status': recognition_status})
