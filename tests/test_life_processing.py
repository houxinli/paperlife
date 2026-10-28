import sys, tempfile, unittest, json, sqlite3
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def legacy_job(store, session, profile, status):
    with store.db() as db:
        db.execute(
            'INSERT INTO processing_jobs(session,profile,input_hash,snapshot,status) VALUES(?,?,?,?,?)',
            (session, profile, 'hash', '[]', status),
        )


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_session_missing_job_recovers_and_keeps_full_turn(self):
        from paperlife.lifebook.store import Store
        from paperlife.lifebook.worker import recover_interrupted

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            p = store.create_profile(1, '测试', '')
            s = store.create_session(p['id'], 1, 'test')
            original = '完整转写' * 4300 + '末尾不能丢'
            store.add_turn(s['id'], 'user', original, 0)
            store.finish_session(s['id'], 'ended')
            self.assertEqual(store.turns(s['id'])[0]['text'], original)
            recover_interrupted(store)
            with store.db() as db:
                job = dict(db.execute('SELECT * FROM writing_jobs').fetchone())
            result = ''.join(t['text'] for t in json.loads(job['snapshot'])['materials'])
            self.assertEqual(result, original)

    async def test_worker_logs_failures_and_keeps_running(self):
        import asyncio
        from paperlife.lifebook import worker
        from paperlife.lifebook.store import Store

        calls = []

        async def broken(store):
            calls.append(1)
            if len(calls) > 1:
                raise asyncio.CancelledError
            raise RuntimeError('虚构失败')

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            with (
                patch.object(worker, 'process_one', broken),
                patch.object(worker.asyncio, 'sleep', return_value=None),
                self.assertLogs('paperlife.lifebook.worker', 'ERROR') as logs,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await worker.worker(store)
            self.assertEqual(len(calls), 2)
            self.assertIn('life worker iteration failed', logs.output[0])


class SchemaTests(unittest.TestCase):
    def test_new_database_records_latest_version_and_migrations_run_once(self):
        from paperlife.lifebook.store import Store
        from paperlife.lifebook.schema import MIGRATIONS

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            with store.db() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], len(MIGRATIONS))
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            p = store.create_profile(1, '测试', '虚构背景')
            Store(Path(tmp))
            with store.db() as db:
                self.assertEqual(
                    db.execute('SELECT count(*) FROM sources WHERE profile=?', (p['id'],)).fetchone()[0], 1
                )

    def test_unversioned_legacy_database_is_upgraded_in_place(self):
        from paperlife.lifebook.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            with sqlite3.connect(Path(tmp) / 'life.sqlite3') as db:
                db.execute(
                    'CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, context TEXT NOT NULL, owner INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)'
                )
                db.execute("INSERT INTO profiles(id,name,context,owner) VALUES('p1','测试','虚构旧背景',1)")
            store = Store(Path(tmp))
            with store.db() as db:
                columns = {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}
                self.assertIn('voice_dialect', columns)
                self.assertEqual(db.execute("SELECT text FROM sources WHERE profile='p1'").fetchone()[0], '虚构旧背景')

    def test_newer_database_version_is_rejected(self):
        from paperlife.lifebook.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            with sqlite3.connect(Path(tmp) / 'life.sqlite3') as db:
                db.execute('PRAGMA user_version=999')
            with self.assertRaises(RuntimeError):
                Store(Path(tmp))

    def test_legacy_queue_retired_unfinished_jobs_requeued_completed_kept(self):
        from paperlife.lifebook import schema
        from paperlife.lifebook.store import Store
        from paperlife.lifebook.editor_jobs import enqueue_missing

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            p = store.create_profile(1, '测试', '')
            sessions = {}
            for status in ('pending', 'running', 'failed', 'completed'):
                s = store.create_session(p['id'], 1, status)
                store.add_turn(s['id'], 'user', '人工虚构的测试句子', 0)
                store.finish_session(s['id'], 'ended')
                legacy_job(store, s['id'], p['id'], status)
                sessions[status] = s['id']
            with store.db() as db:
                db.execute('PRAGMA user_version=1')
            with store.db() as db:
                schema.migrate(db)
                statuses = dict(db.execute('SELECT session,status FROM processing_jobs').fetchall())
            self.assertEqual(statuses[sessions['completed']], 'completed')
            self.assertEqual({statuses[sessions[k]] for k in ('pending', 'running', 'failed')}, {'superseded'})
            enqueue_missing(store)
            with store.db() as db:
                queued = {
                    r[0]
                    for r in db.execute(
                        "SELECT json_extract(snapshot,'$.sources[0].id') FROM writing_jobs WHERE kind='material'"
                    )
                }
            self.assertEqual(queued, {sessions[k] for k in ('pending', 'running', 'failed')})


class ModelJsonTests(unittest.TestCase):
    def test_missing_final_brackets_repaired_without_changing_content(self):
        from paperlife.lifebook.editor_models import parse_output

        raw = '{"stories":[{"title":"测试","paragraphs":[{"text":"原文不变","citations":[{"turn_id":1,"quote":"原文不变"}]}]}'
        self.assertEqual(parse_output(raw)['stories'][0]['paragraphs'][0]['text'], '原文不变')

    def test_truncated_string_or_mismatched_brackets_rejected(self):
        from paperlife.lifebook.editor_models import parse_output

        for raw in ['{"items":[{"title":"没有结束', '{"items":[}', '{"items":']:
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_output(raw)


class ShortAnswerTests(unittest.TestCase):
    def test_short_answer_keeps_question_context_and_exact_evidence(self):
        from paperlife.lifebook.store import Store
        from paperlife.lifebook.materials import snapshot, checked_evidence

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            p = store.create_profile(1, '测试', '')
            s = store.create_session(p['id'], 1, 'test')
            store.add_turn(s['id'], 'assistant', '这是虚构测试。老师叫什么名字？', 0)
            store.add_turn(s['id'], 'user', '甲乙', 2)
            store.finish_session(s['id'], 'ended')
            units = snapshot(store, p['id'], [{'kind': 'session', 'id': s['id']}])['materials']
            self.assertIn('老师叫什么名字', units[0]['context'])
            evidence = checked_evidence([{'unit_id': units[0]['unit_id'], 'quote': '甲乙'}], units)
            self.assertEqual(evidence[0]['quote'], '甲乙')

    def test_short_fragment_of_long_turn_is_not_sufficient_evidence(self):
        from paperlife.lifebook.materials import checked_evidence, MaterialError

        with self.assertRaises(MaterialError):
            checked_evidence(
                [{'unit_id': 'u1', 'quote': '甲乙'}], [{'unit_id': 'u1', 'text': '甲乙是人工虚构测试名称。'}]
            )
