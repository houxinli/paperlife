import unittest, tempfile
from pathlib import Path
from fastapi.testclient import TestClient


class AuthTests(unittest.TestCase):
    def setUp(self):
        from paperlife.main import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name), start_worker=False)
        self.auth = self.app.state.auth
        self.auth.create_user('owner', 'long-test-password', 'admin')
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_login_logout_and_profile_auth(self):
        self.assertEqual(self.client.get('/api/life/profiles').status_code, 401)
        self.assertEqual(
            self.client.post('/api/auth/login', json={'username': 'owner', 'password': 'wrong'}).status_code, 401
        )
        r = self.client.post('/api/auth/login', json={'username': 'owner', 'password': 'long-test-password'})
        self.assertEqual(r.status_code, 200)
        token = r.json()['session_id']
        h = {'Authorization': 'Bearer ' + token}
        self.assertEqual(self.client.get('/api/auth/me', headers=h).json()['role'], 'admin')
        self.assertEqual(self.client.post('/api/life/profiles', headers=h, json={'name': '虚构档案'}).status_code, 200)
        self.client.post('/api/auth/logout', headers=h)
        self.assertEqual(self.client.get('/api/auth/me', headers=h).status_code, 401)

    def test_external_login_disabled_until_configured(self):
        self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'untrusted'}).status_code, 503)
        self.assertEqual(self.client.post('/api/auth/sms/send', json={'phone': '+8613800000000'}).status_code, 503)

    def test_otp_is_single_use_limited_and_never_stored_plaintext(self):
        from fastapi import HTTPException

        phone = '+8613800000000'
        code = self.auth.make_otp(phone, 'test-ip')
        with self.auth.db() as db:
            self.assertNotEqual(db.execute('SELECT digest FROM otp').fetchone()['digest'], code)
        with self.assertRaises(HTTPException):
            self.auth.make_otp(phone, 'test-ip')
        for _ in range(5):
            self.assertFalse(self.auth.consume_otp(phone, 'invalid'))
        self.assertFalse(self.auth.consume_otp(phone, code))
        other = '+8613900000000'
        second = self.auth.make_otp(other, 'other-ip')
        self.assertTrue(self.auth.consume_otp(other, second))
        self.assertFalse(self.auth.consume_otp(other, second))

    def test_wechat_server_result_controls_identity_not_client_openid(self):
        import os
        from unittest.mock import patch, AsyncMock

        with patch.dict(os.environ, {'WECHAT_APP_ID': 'test-app', 'WECHAT_APP_SECRET': 'test-secret'}):
            with patch(
                'paperlife.identity_providers.exchange_wechat',
                new=AsyncMock(return_value=('test-app', 'verified-openid')),
            ):
                r = self.client.post('/api/auth/wechat', json={'code': 'one-time-code', 'openid': 'forged-admin'})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['user']['role'], 'user')
                with self.auth.db() as db:
                    self.assertEqual(
                        db.execute('SELECT subject FROM identities').fetchone()['subject'], 'verified-openid'
                    )

    def test_logout_invalidates_unconsumed_voice_ticket(self):
        r = self.client.post('/api/auth/login', json={'username': 'owner', 'password': 'long-test-password'}).json()
        h = {'Authorization': 'Bearer ' + r['session_id']}
        self.client.put('/api/life/provider', headers=h, json={'api_key': 'test-only'})
        p = self.client.post('/api/life/profiles', headers=h, json={'name': '虚构人物'}).json()
        ticket = self.client.post(
            '/api/life/profiles/' + p['id'] + '/sessions', headers=h, json={'topic': 'test'}
        ).json()['ticket']
        self.client.post('/api/auth/logout', headers=h)
        with self.client.websocket_connect('/api/life/live', headers={'origin': 'http://testserver'}) as ws:
            ws.send_json({'ticket': ticket})
            self.assertEqual(ws.receive_json()['type'], 'error')

    def test_shared_ip_does_not_lock_other_accounts_but_account_limit_holds(self):
        from fastapi import HTTPException

        for _ in range(10):
            self.auth.record_attempt('password', 'fictional-a', 'family-nat', 10, 300)
        with self.assertRaises(HTTPException):
            self.auth.record_attempt('password', 'fictional-a', 'other-ip', 10, 300)
        self.auth.record_attempt('password', 'fictional-b', 'family-nat', 10, 300)
        for index in range(39):
            self.auth.record_attempt('password', f'fictional-{index}', 'family-nat', 10, 300)
        with self.assertRaises(HTTPException):
            self.auth.record_attempt('password', 'fictional-new', 'family-nat', 10, 300)

    def test_wechat_rate_limits_are_independent_between_ips(self):
        for _ in range(30):
            self.auth.record_attempt('wechat', 'ip:attacker', 'attacker', 30, 300)
        self.auth.record_attempt('wechat', 'ip:legitimate', 'legitimate', 30, 300)
