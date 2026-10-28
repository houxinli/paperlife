import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from paperlife.lifebook.api import build_router
from paperlife.lifebook.store import Store


class NativeTicketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / 'main.db'
        app = FastAPI()
        app.include_router(
            build_router(
                Store(Path(db).parent / 'life'), lambda: SimpleNamespace(id=1, role='admin'), lambda uid: uid == 1
            )
        )
        self.client = TestClient(app)
        self.client.put('/life/provider', json={'api_key': 'fictional'})
        self.p = self.client.post('/life/profiles', json={'name': '虚构测试'}).json()

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def ticket(self, transport='web'):
        return self.client.post(
            '/life/profiles/' + self.p['id'] + '/sessions', json={'topic': '虚构测试', 'transport': transport}
        ).json()['ticket']

    async def fake_interview(self, ws, *args, **kwargs):
        await ws.send_json({'type': 'ready'})

    def test_rejected_connection_is_logged_without_ticket(self):
        ticket = self.ticket('web')
        with self.assertLogs('paperlife.lifebook.api', 'WARNING') as logs:
            with self.client.websocket_connect('/life/live') as ws:
                ws.send_json({'ticket': ticket})
                self.assertEqual(ws.receive_json()['type'], 'error')
        self.assertIn('ValueError', logs.output[0])
        self.assertNotIn(ticket, logs.output[0])

    def test_rejected_connection_does_not_leave_session_connecting(self):
        started = self.client.post(
            '/life/profiles/' + self.p['id'] + '/sessions', json={'topic': '虚构测试', 'transport': 'web'}
        ).json()
        with self.client.websocket_connect('/life/live') as ws:
            ws.send_json({'ticket': started['ticket']})
            self.assertEqual(ws.receive_json()['type'], 'error')
        session = self.client.get('/life/sessions/' + started['id']).json()
        self.assertEqual(session['status'], 'interrupted')

    def test_native_ticket_allows_absent_origin_but_is_one_use(self):
        ticket = self.ticket('wechat')
        with patch('paperlife.lifebook.realtime.interview', self.fake_interview):
            with self.client.websocket_connect('/life/live') as ws:
                ws.send_json({'ticket': ticket})
                self.assertEqual(ws.receive_json()['type'], 'ready')
            with self.client.websocket_connect('/life/live') as ws:
                ws.send_json({'ticket': ticket})
                self.assertEqual(ws.receive_json()['type'], 'error')

    def test_regular_web_ticket_without_origin_is_rejected(self):
        ticket = self.ticket()
        with self.client.websocket_connect('/life/live') as ws:
            ws.send_json({'ticket': ticket})
            self.assertEqual(ws.receive_json()['type'], 'error')

    def test_native_ticket_does_not_allow_arbitrary_origin(self):
        ticket = self.ticket('wechat')
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/life/live', headers={'origin': 'https://unrelated.example'}) as ws:
                ws.send_json({'ticket': ticket})

    def test_missing_or_expired_ticket_is_not_native_auth(self):
        with self.client.websocket_connect('/life/live') as ws:
            ws.send_json({'ticket': 'invented', 'transport': 'wechat'})
            self.assertEqual(ws.receive_json()['type'], 'error')


class SessionDigestTests(unittest.TestCase):
    def test_digest_uses_host_cookie_and_prefers_bearer(self):
        import hashlib
        from starlette.requests import Request
        from paperlife.lifebook.access import Access

        def request(*headers):
            return Request({'type': 'http', 'headers': [(k.encode(), v.encode()) for k, v in headers]})

        access = Access(None, session_cookie='host_session')
        digest = lambda token: hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(access.session_digest(request(('cookie', 'host_session=fictional'))), digest('fictional'))
        self.assertEqual(access.session_digest(request(('cookie', 'paperlife_session=fictional'))), digest(''))
        self.assertEqual(
            access.session_digest(request(('authorization', 'Bearer bearer-token'), ('cookie', 'host_session=x'))),
            digest('bearer-token'),
        )
