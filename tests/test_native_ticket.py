import tempfile, sqlite3, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from paperlife.lifebook.api import build_router


class NativeTicketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / 'main.db'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,is_active INTEGER)')
            c.execute('INSERT INTO users VALUES(1,1)')
        app = FastAPI()
        app.include_router(build_router(db, lambda: SimpleNamespace(id=1, role='admin')))
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
