"""独立的私有编辑模型配置与有限调用；不承担 HTTP 用户授权。"""
import asyncio
from contextlib import suppress
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

import httpx
from websockets.asyncio.client import connect

from .generation import parse_output
from .protocol import decode, encode
from .writing_rules import PROMPT_VERSION, prompt_hash, stage_prompt, task_message

MAX_INPUT_BYTES = 240_000
MAX_OUTPUT_BYTES = 160_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_TOKENS = 8192
TIMEOUT_SECONDS = 120
ROLES = ('extractor', 'writer')
# 官方固定地址，任何配置均不可传入 endpoint/base_url，也不跟随重定向。
ENDPOINTS = {
    'deepseek': 'https://api.deepseek.com/chat/completions',
    'ark': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
}
REALTIME_ENDPOINT = 'wss://openspeech.bytedance.com/api/v3/realtime/dialogue'
DEFAULT = {'provider': 'doubao_realtime', 'model': '1.2.1.1'}


def _role_config(data, existing=None):
    if not isinstance(data, dict) or set(data) - {'provider', 'model', 'api_key'}:
        raise ValueError('invalid_editor_config')
    provider, model = data.get('provider'), data.get('model')
    if provider not in ('doubao_realtime', *ENDPOINTS):
        raise ValueError('invalid_editor_provider')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', model):
        raise ValueError('invalid_editor_model')
    if provider == 'doubao_realtime' and model != DEFAULT['model']:
        raise ValueError('invalid_editor_model')
    key = data.get('api_key', '')
    if not isinstance(key, str) or len(key) > 2000 or any(ord(ch) < 32 or ord(ch) > 126 for ch in key):
        raise ValueError('invalid_editor_key')
    key = key.strip()
    if provider == 'doubao_realtime' and key:
        raise ValueError('editor_uses_speech_credentials')
    if not key and existing and existing['provider'] == provider:
        key = existing.get('api_key', '')
    return {'provider': provider, 'model': model, 'api_key': key}


def _read_config(store):
    path = Path(store.root) / 'editor-models.json'
    if not path.exists():
        return {role: dict(DEFAULT, api_key='') for role in ROLES}
    try:
        if path.stat().st_size > 12_000:
            raise ValueError('invalid_editor_config')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) - set(ROLES):
            raise ValueError('invalid_editor_config')
        return {role: _role_config(data.get(role, DEFAULT)) for role in ROLES}
    except (ValueError, TypeError, OSError):
        raise ValueError('invalid_editor_config') from None


def _speech_credentials(store):
    try:
        cfg = store.provider()
        fields = {name: cfg.get(name, '') for name in ('api_key', 'app_id', 'access_token')}
        for value in fields.values():
            if not isinstance(value, str) or len(value) > 2000 or any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
                raise ValueError('invalid_speech_credentials')
        return fields
    except (ValueError, TypeError, AttributeError, OSError):
        raise ValueError('invalid_speech_credentials') from None


def _configured(store, config):
    if config['provider'] != 'doubao_realtime':
        return bool(config.get('api_key'))
    speech = _speech_credentials(store)
    return bool(speech['api_key'] or (speech['app_id'] and speech['access_token']))


def public_config(store, can_configure):
    configs = _read_config(store)
    return {**{role: {'provider': configs[role]['provider'], 'model': configs[role]['model'],
                      'configured': _configured(store, configs[role])} for role in ROLES},
            'can_configure': bool(can_configure)}


def save_config(store, data):
    """调用者必须已验证管理员身份。单角色更新也须保留另一角色。"""
    if not isinstance(data, dict) or not data or set(data) - set(ROLES):
        raise ValueError('invalid_editor_config')
    root = Path(store.root)
    root.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(root / '.editor-models.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        configs = _read_config(store)
        for role, update in data.items():
            configs[role] = _role_config(update, configs[role])
        fd, temp_path = tempfile.mkstemp(prefix='.editor-models-', dir=root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as temp:
                json.dump(configs, temp, ensure_ascii=False)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, root / 'editor-models.json')
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_path)
    return public_config(store, True)


def _text(value, limit=12_000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('invalid_editor_output')


def _list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('invalid_editor_output')
    return value


def _citations(value):
    if not _list(value, 80):
        raise ValueError('invalid_editor_output')
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('invalid_editor_output')
        _text(item.get('unit_id'), 300)
        _text(item.get('quote'))


def _validate_output(stage, value):
    if not isinstance(value, dict):
        raise ValueError('invalid_editor_output')
    if stage == 'extract':
        for item in _list(value.get('items'), 80):
            if not isinstance(item, dict) or item.get('kind') not in ('person', 'event', 'fact', 'question'):
                raise ValueError('invalid_editor_output')
            _text(item.get('title'), 300)
            _text(item.get('body'))
            _citations(item.get('citations'))
            for field in ('time_text', 'people', 'place'):
                if field in item and (not isinstance(item[field], str) or len(item[field]) > 2000):
                    raise ValueError('invalid_editor_output')
    elif stage == 'outline':
        for item in _list(value.get('outline'), 40):
            if not isinstance(item, dict):
                raise ValueError('invalid_editor_output')
            _text(item.get('title'), 300)
    elif stage == 'audit':
        if type(value.get('ok')) is not bool:
            raise ValueError('invalid_editor_output')
        issues = _list(value.get('issues'), 120)
        if value['ok'] != (not issues):
            raise ValueError('invalid_editor_output')
        for issue in issues:
            if not isinstance(issue, dict):
                raise ValueError('invalid_editor_output')
            _text(issue.get('reason'))
    else:
        _text(value.get('title'), 300)
        for paragraph in _list(value.get('paragraphs'), 120):
            if not isinstance(paragraph, dict):
                raise ValueError('invalid_editor_output')
            _text(paragraph.get('text'))
            _citations(paragraph.get('citations'))
    return value


async def _http_text(config, instruction, material):
    request = {'model': config['model'], 'messages': [{'role': 'system', 'content': instruction},
                {'role': 'user', 'content': material}], 'response_format': {'type': 'json_object'},
               'max_tokens': MAX_TOKENS, 'stream': False}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=15),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream('POST', ENDPOINTS[config['provider']],
                                     headers={'Authorization': 'Bearer ' + config['api_key']}, json=request) as response:
                if response.status_code != 200:
                    raise ValueError('editor_provider_response')
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ValueError('editor_output_limit')
        data = json.loads(chunks)
        choice = data['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            raise ValueError('editor_provider_incomplete')
        return choice['message']['content']
    except (httpx.HTTPError, KeyError, IndexError, TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError('editor_provider_response') from None


async def _realtime_text(store, config, instruction, material):
    speech = _speech_credentials(store)
    sid = uuid.uuid4().hex
    headers = {'X-Api-Resource-Id': 'volc.speech.dialog', 'X-Api-Connect-Id': sid}
    if speech['api_key']:
        headers['X-Api-Key'] = speech['api_key']
    else:
        headers.update({'X-Api-App-ID': speech['app_id'], 'X-Api-Access-Key': speech['access_token'],
                        'X-Api-App-Key': 'PlgvMymc7f3tQnJ6'})
    request = {'dialog': {'system_role': instruction, 'extra': {'model': config['model'], 'input_mod': 'text',
                'enable_volc_websearch': False}},
               'tts': {'audio_config': {'format': 'pcm_s16le', 'sample_rate': 24000, 'channel': 1}}}
    try:
        async with connect(REALTIME_ENDPOINT, additional_headers=headers, open_timeout=15, max_size=MAX_RESPONSE_BYTES) as ws:
            try:
                await ws.send(encode(1, {}))
                if decode(await ws.recv())['event'] != 50:
                    raise ValueError('editor_provider_connection')
                await ws.send(encode(100, request, sid))
                if decode(await ws.recv())['event'] != 150:
                    raise ValueError('editor_provider_session')
                await ws.send(encode(501, {'content': material}, sid))
                result, size = [], 0
                while True:
                    event = decode(await ws.recv())
                    if event['event'] == 550:
                        content = event['data'].get('content', '')
                        if not isinstance(content, str):
                            raise ValueError('editor_provider_response')
                        size += len(content.encode('utf-8'))
                        if size > MAX_OUTPUT_BYTES:
                            raise ValueError('editor_output_limit')
                        result.append(content)
                    elif event['event'] == 559:
                        return ''.join(result)
                    elif event['event'] in (51, 153, 599):
                        raise ValueError('editor_provider_response')
            finally:
                # 终止帧失败也不能覆盖原错误或取消；外层总时限仍有效。
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await ws.send(encode(102, {}, sid))
                        await ws.send(encode(2, {}))
    except (OSError, KeyError, TypeError):
        raise ValueError('editor_provider_response') from None


async def generate_editor(store, stage, payload):
    instruction = stage_prompt(stage)
    if not isinstance(payload, dict):
        raise ValueError('invalid_editor_payload')
    try:
        material = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError):
        raise ValueError('invalid_editor_payload') from None
    if len(material.encode('utf-8')) > MAX_INPUT_BYTES:
        raise ValueError('editor_input_limit')
    material = task_message(stage, material)
    config = _read_config(store)['extractor' if stage == 'extract' else 'writer']
    if not _configured(store, config):
        raise ValueError('provider_unconfigured')
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            if config['provider'] == 'doubao_realtime':
                text = await _realtime_text(store, config, instruction, material)
            else:
                text = await _http_text(config, instruction, material)
    except TimeoutError:
        raise ValueError('editor_provider_timeout') from None
    except Exception as error:
        # 第三方异常可能含请求头、URL、错误正文；只放行本模块的固定错误码。
        if type(error) is ValueError and str(error).startswith('editor_'):
            raise
        raise ValueError('editor_provider_response') from None
    if not isinstance(text, str):
        raise ValueError('invalid_editor_output')
    if len(text.encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise ValueError('editor_output_limit')
    try:
        output = _validate_output(stage, parse_output(text.strip()))
        # 标准库解析器默认接受 NaN/Infinity，公开输出契约必须仍是标准 JSON。
        json.dumps(output, allow_nan=False)
    except (ValueError, TypeError, RecursionError, KeyError):
        raise ValueError('invalid_editor_output') from None
    return {'output': output, 'metadata': {'provider': config['provider'], 'model': config['model'],
            'prompt_version': PROMPT_VERSION, 'prompt_hash': prompt_hash(stage)}}
