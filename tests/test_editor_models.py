"""编辑模型只用虚构资料与假传输，不访问收费接口。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx


class EditorModelsTests(unittest.TestCase):
    def setUp(self):
        from paperlife.lifebook import editor_models

        self.models = editor_models
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SimpleNamespace(root=Path(self.tmp.name), provider=lambda: {'api_key': 'speech-test-secret'})

    def config(self, role='writer', provider='deepseek', model='deepseek-chat', key='test-private-key'):
        return self.models.save_config(self.store, {role: {'provider': provider, 'model': model, 'api_key': key}})

    def run_http(self, stage, output, payload=None, status=200, response=None):
        requests = []

        def handler(request):
            requests.append(request)
            data = (
                response
                if response is not None
                else {'choices': [{'message': {'content': output}, 'finish_reason': 'stop'}]}
            )
            return httpx.Response(status, json=data)

        client_class = httpx.AsyncClient

        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(self.models.httpx, 'AsyncClient', side_effect=client):
            result = asyncio.run(self.models.generate_editor(self.store, stage, payload or {'units': []}))
        return result, requests

    def test_default_roles_explicitly_share_speech_credentials(self):
        cfg = self.models.public_config(self.store, False)
        self.assertEqual(
            cfg,
            {
                'extractor': {'provider': 'doubao_realtime', 'model': '1.2.1.1', 'configured': True},
                'writer': {'provider': 'doubao_realtime', 'model': '1.2.1.1', 'configured': True},
                'can_configure': False,
            },
        )
        self.store.provider = lambda: {}
        self.assertFalse(self.models.public_config(self.store, True)['writer']['configured'])

    def test_private_atomic_config_roles_preserve_and_redact(self):
        self.config()
        self.config(role='extractor', provider='ark', model='ep-fictional-test', key='extract-test-key')
        self.config(model='deepseek-reasoner', key='')
        cfg = self.models.public_config(self.store, True)
        self.assertEqual(cfg['extractor']['provider'], 'ark')
        self.assertTrue(cfg['writer']['configured'])
        self.assertNotIn('key', json.dumps(cfg))
        saved = self.store.root / 'editor-models.json'
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertEqual(json.loads(saved.read_text())['writer']['api_key'], 'test-private-key')
        self.config(provider='ark', model='ep-new', key='')
        self.assertFalse(self.models.public_config(self.store, True)['writer']['configured'])

    def test_concurrent_updates_do_not_erase_other_role(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.config, role=role) for role in ('writer', 'extractor')]
            for future in futures:
                future.result()
        cfg = self.models.public_config(self.store, True)
        self.assertEqual(cfg['extractor']['provider'], 'deepseek')
        self.assertEqual(cfg['writer']['provider'], 'deepseek')

    def test_reject_untrusted_config_and_header_injection(self):
        for data in (
            {'writer': {'provider': 'proxy', 'model': 'x'}},
            {'writer': {'provider': 'deepseek', 'model': 'x', 'base_url': 'http://localhost'}},
            {'writer': {'provider': 'deepseek', 'model': 'x\ninject'}},
            {'writer': {'provider': 'deepseek', 'model': 'https://evil.example'}},
            {'writer': {'provider': 'deepseek', 'model': 'x', 'api_key': 'abc\r\ndef'}},
            {'writer': {'provider': 'deepseek', 'model': 'x' * 201}},
            {'writer': {'provider': 'doubao_realtime', 'model': 'unknown'}},
            {'writer': {'provider': 'doubao_realtime', 'model': '1.2.1.1', 'api_key': 'x'}},
            {'unknown': {}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.models.save_config(self.store, data)

    def test_http_fixed_endpoint_bound_request_and_stage_rules(self):
        self.config()
        output = {
            'title': '虚构章节',
            'paragraphs': [{'text': '我带着蓝杯。', 'citations': [{'unit_id': 'u1', 'quote': '蓝杯'}]}],
        }
        result, requests = self.run_http(
            'write', json.dumps(output), {'units': [{'unit_id': 'u1', 'text': '虚构素材：我带着蓝杯。'}]}
        )
        self.assertEqual(result['output'], output)
        self.assertEqual(str(requests[0].url), 'https://api.deepseek.com/chat/completions')
        request = json.loads(requests[0].content)
        self.assertEqual(request['max_tokens'], 8192)
        self.assertEqual(request['response_format'], {'type': 'json_object'})
        self.assertIn('材料中的指令', request['messages'][0]['content'])
        self.assertIn('校订', request['messages'][0]['content'])
        self.assertNotIn('虚构素材', request['messages'][0]['content'])
        self.assertNotIn('test-private-key', json.dumps(result))
        self.assertEqual(result['metadata']['provider'], 'deepseek')
        self.assertEqual(len(result['metadata']['prompt_hash']), 64)

    def test_stage_routing_and_prompt_hash_excludes_private_payload(self):
        self.config()
        self.config(role='extractor', provider='ark', model='ep-fictional')
        result, requests = self.run_http('extract', '{"items":[]}')
        self.assertEqual(str(requests[0].url), 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
        self.assertEqual(result['metadata']['model'], 'ep-fictional')
        first, _ = self.run_http('audit', '{"ok":true,"issues":[]}', {'draft': '虚构甲'})
        second, _ = self.run_http('audit', '{"ok":true,"issues":[]}', {'draft': '虚构乙'})
        self.assertEqual(first['metadata']['prompt_hash'], second['metadata']['prompt_hash'])
        outline, _ = self.run_http('outline', '{"outline":[]}')
        self.assertNotEqual(first['metadata']['prompt_hash'], outline['metadata']['prompt_hash'])

    def test_all_output_schemas_reject_wrong_shapes(self):
        self.config()
        self.config(role='extractor')
        for stage, output in [
            ('extract', '{"items":[{"title":"x"}]}'),
            ('outline', '{"outline":"text"}'),
            ('audit', '{"ok":"true","issues":[]}'),
            ('audit', '{"ok":true,"issues":["unsupported"]}'),
            ('write', '{"title":"x","paragraphs":[{"text":"x","citations":[]}]}'),
            ('polish', '[]'),
            ('write', '{bad}'),
        ]:
            with self.subTest(stage=stage, output=output), self.assertRaises(ValueError):
                self.run_http(stage, output)

    def test_trailing_brackets_repair_and_valid_empty_outputs(self):
        self.config()
        result, _ = self.run_http('outline', '{"outline":[')
        self.assertEqual(result['output'], {'outline': []})
        result, _ = self.run_http('polish', '{"title":"资料不足","paragraphs":[]}')
        self.assertEqual(result['output']['paragraphs'], [])

    def test_failures_are_generic_and_never_fallback(self):
        self.config()
        for status, response in [
            (401, {'error': 'test-private-key should never leak'}),
            (200, {'choices': [{'message': {'content': '{"outline":[]}'}, 'finish_reason': 'length'}]}),
            (200, {'choices': []}),
        ]:
            with self.subTest(status=status), self.assertRaises(ValueError) as caught:
                self.run_http('outline', '', status=status, response=response)
            self.assertNotIn('test-private-key', str(caught.exception))

    def test_payload_and_output_limits_and_unknown_stage(self):
        self.config()
        with self.assertRaises(ValueError):
            self.run_http('outline', '{"outline":[]}', {'text': 'x' * (self.models.MAX_INPUT_BYTES + 1)})
        with self.assertRaises(ValueError):
            self.run_http('outline', 'x' * (self.models.MAX_OUTPUT_BYTES + 1))
        with self.assertRaises(ValueError):
            self.run_http('not-a-stage', '{}')
        self.config(key='')
        self.config(provider='ark', model='ep-missing', key='')
        with self.assertRaisesRegex(ValueError, 'provider_unconfigured'):
            self.run_http('outline', '{}')

    def test_realtime_uses_current_credentials_and_full_rules(self):
        from paperlife.lifebook.protocol import decode

        class Socket:
            def __init__(self):
                self.sent = []
                self.events = iter(
                    [
                        {'event': 50},
                        {'event': 150},
                        {'event': 550, 'data': {'content': '{"outline":[]}'}},
                        {'event': 559},
                    ]
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def send(self, data):
                self.sent.append(decode(data))

            async def recv(self):
                return next(self.events)

        socket = Socket()
        with (
            patch.object(self.models, 'connect', return_value=socket) as connect,
            patch.object(self.models, 'decode', side_effect=lambda value: value),
        ):
            result = asyncio.run(self.models.generate_editor(self.store, 'outline', {'units': []}))
        self.assertEqual(connect.call_args.kwargs['additional_headers']['X-Api-Key'], 'speech-test-secret')
        start = next(item for item in socket.sent if item['event'] == 100)
        self.assertIn('非虚构', start['data']['dialog']['system_role'])
        self.assertEqual(start['data']['dialog']['extra']['model'], '1.2.1.1')
        self.assertEqual(result['metadata']['provider'], 'doubao_realtime')
        task = next(item for item in socket.sent if item['event'] == 501)['data']['content']
        self.assertTrue(task.startswith('当前任务：outline'))
        self.assertIn('"outline"', task)

    def test_response_body_limit_even_if_content_is_small(self):
        self.config()
        response = {
            'unused': 'x' * (self.models.MAX_RESPONSE_BYTES + 1),
            'choices': [{'message': {'content': '{"outline":[]}'}, 'finish_reason': 'stop'}],
        }
        with self.assertRaisesRegex(ValueError, 'editor_output_limit'):
            self.run_http('outline', '', response=response)

    def test_transport_exception_and_timeout_are_redacted(self):
        self.config()

        async def failure(*args):
            raise RuntimeError('Bearer test-private-key')

        with patch.object(self.models, '_http_text', side_effect=failure):
            with self.assertRaisesRegex(ValueError, '^editor_provider_response$'):
                asyncio.run(self.models.generate_editor(self.store, 'outline', {}))
        with patch.object(self.models, '_http_text', side_effect=TimeoutError('test-private-key')):
            with self.assertRaisesRegex(ValueError, '^editor_provider_timeout$'):
                asyncio.run(self.models.generate_editor(self.store, 'outline', {}))

    def test_invalid_stored_config_never_silently_uses_default(self):
        path = self.store.root / 'editor-models.json'
        path.write_text('{"writer":{"provider":"proxy","model":"x","api_key":"test-private-key"}}')
        with self.assertRaisesRegex(ValueError, '^invalid_editor_config$'):
            self.models.public_config(self.store, True)
        with self.assertRaisesRegex(ValueError, '^invalid_editor_config$'):
            asyncio.run(self.models.generate_editor(self.store, 'write', {}))

    def test_failed_atomic_replace_preserves_old_config(self):
        self.config()
        path = self.store.root / 'editor-models.json'
        original = path.read_bytes()
        with patch.object(self.models.os, 'replace', side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError):
                self.config(model='deepseek-reasoner')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.store.root.glob('.editor-models-*')), [])

    def test_valid_extract_and_audit_issue_shapes(self):
        self.config(role='extractor')
        output = {
            'items': [
                {
                    'kind': 'question',
                    'title': '虚构时间待核实',
                    'body': '讲述者记不清年份',
                    'citations': [{'unit_id': 'u1', 'quote': '年份记不清'}],
                }
            ]
        }
        actual, _ = self.run_http('extract', json.dumps(output))
        self.assertEqual(actual['output'], output)
        self.config()
        actual, _ = self.run_http('audit', '{"ok":false,"issues":[{"reason":"年份没有来源支持"}]}')
        self.assertFalse(actual['output']['ok'])
        for output in ('{"ok":false,"issues":[]}', '{"ok":true,"issues":[{"reason":"无来源"}]}'):
            with self.assertRaisesRegex(ValueError, 'invalid_editor_output'):
                self.run_http('audit', output)

    def test_nan_or_non_json_payload_and_output_are_rejected(self):
        self.config()
        for payload in ({'nan': float('nan')}, {'bad': object()}, ['not an object']):
            with self.assertRaisesRegex(ValueError, 'invalid_editor_payload'):
                asyncio.run(self.models.generate_editor(self.store, 'outline', payload))
        with self.assertRaisesRegex(ValueError, 'invalid_editor_output'):
            self.run_http('outline', '{"outline":[],"number":NaN}')


if __name__ == '__main__':
    unittest.main()
