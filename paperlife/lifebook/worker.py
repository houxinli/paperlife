"""后台整理 worker 与启动恢复；单进程部署下只运行一个实例。"""

import asyncio
import json
import logging

from .editor_jobs import enqueue_missing, process_one

log = logging.getLogger(__name__)


async def worker(store):
    while True:
        try:
            enqueue_missing(store)
            await process_one(store)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 单个任务失败不能停止后续整理；任务状态仍通过 jobs 接口可见。
            log.exception('life worker iteration failed')
        await asyncio.sleep(3)


def recover_interrupted(store):
    # 当前生产部署为单进程；启动前不存在可继续使用的旧WebSocket/ticket。
    with store.db() as db:
        db.execute("UPDATE sessions SET status='interrupted' WHERE status IN ('active','connecting')")
        # 持久化的累计临时快照在进程重启后不能继续冒充实时/完整识别。
        db.execute(
            "UPDATE listening_originals SET recognition_status=CASE WHEN segments='[]' THEN 'failed' ELSE 'incomplete' END,finalized=1 WHERE finalized=0 AND session IN (SELECT id FROM sessions WHERE mode='listening' AND status='interrupted')"
        )
        # 崩溃前若只有临时快照，恢复可读的声音组原文，避免历史显示空白。
        for row in db.execute(
            "SELECT session,segments FROM listening_originals WHERE finalized=1 AND recognition_status IN ('failed','incomplete') AND NOT EXISTS(SELECT 1 FROM turns WHERE turns.session=listening_originals.session)"
        ).fetchall():
            for segment in json.loads(row['segments']):
                if segment['text'].strip():
                    db.execute(
                        'INSERT INTO turns(session,role,text,seconds) VALUES(?,?,?,?)',
                        (
                            row['session'],
                            'user',
                            f"声音组 {segment['speaker_id']}：{segment['text']}",
                            segment['start_ms'] / 1000,
                        ),
                    )
    enqueue_missing(store)
