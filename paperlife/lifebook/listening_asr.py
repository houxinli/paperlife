"""火山 ASR 2.0 静默旁听适配。事件是累计快照；终包才代表整段完成。"""

import asyncio
from contextlib import asynccontextmanager, suppress
import gzip
import inspect
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import uuid
import zlib

from websockets.asyncio.client import connect

ENDPOINT = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async'
RESOURCE_ID = 'volc.seedasr.sauc.duration'
_CONNECT_SUPPORTS_PROXY = 'proxy' in inspect.signature(connect).parameters
MAX_AUDIO_FRAME_BYTES = 64_000
MAX_AUDIO_BYTES = 16_000 * 2 * 20 * 60
MAX_RESPONSE_BYTES = 2_000_000
MAX_DECOMPRESSED_BYTES = 4_000_000
MAX_SEGMENTS = 10_000
MAX_TEXT_CHARS = 300_000
AUDIO_CHUNK_BYTES = 6400  # 200ms, 16kHz / mono / signed 16-bit little-endian.


class ASRError(RuntimeError):
    """仅包含固定错误码；不能包含供应商错误正文、密钥或原始识别。"""


def _enabled(store):
    try:
        with (Path(store.root) / 'listening-asr.json').open('r', encoding='utf-8') as file:
            raw = file.read(4097)
        if len(raw) > 4096:
            return False
        data = json.loads(raw)
        return isinstance(data, dict) and set(data) == {'enabled'} and data['enabled'] is True
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return False


def _credentials(store):
    try:
        data = store.provider()
        fields = {key: data.get(key, '') for key in ('api_key', 'app_id', 'access_token')}
        if any(
            not isinstance(value, str) or len(value) > 2000 or any(ord(char) < 33 or ord(char) > 126 for char in value)
            for value in fields.values()
        ):
            return {}
        return fields
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def configured(store):
    """凭据可用性独立于 enabled；这不是资源已开通或真实 API 可用的验证。"""
    fields = _credentials(store)
    return bool(fields.get('api_key') or (fields.get('app_id') and fields.get('access_token')))


def public_config(store, can_configure=False):
    return {
        'enabled': _enabled(store),
        'configured': configured(store),
        'provider': 'volc_seedasr',
        'can_configure': bool(can_configure),
    }


def save_config(store, data):
    """仅保存开关；调用者先验证管理员身份。凭据仍来自已有 provider 配置。"""
    if not isinstance(data, dict) or set(data) != {'enabled'} or type(data['enabled']) is not bool:
        raise ValueError('invalid_listening_asr_config')
    root = Path(store.root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.listening-asr-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump({'enabled': data['enabled']}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, root / 'listening-asr.json')
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
    return public_config(store, True)


def _encode(payload, message_type, *, final=False, json_payload=False):
    compressed = gzip.compress(payload, mtime=0)
    header = bytes([0x11, (message_type << 4) | (2 if final else 0), (0x10 if json_payload else 0) | 1, 0])
    return header + struct.pack('>I', len(compressed)) + compressed


def _decode(frame):
    """有界解码；结束只依据协议 flag / 序号，绝不依据分句 definite。"""
    try:
        if not isinstance(frame, bytes) or not 8 <= len(frame) <= MAX_RESPONSE_BYTES:
            raise ValueError
        if frame[0] != 0x11 or frame[3] != 0:
            raise ValueError
        kind, flags = frame[1] >> 4, frame[1] & 15
        serialization, compression = frame[2] >> 4, frame[2] & 15
        if kind not in (9, 15) or flags not in (0, 1, 2, 3) or compression not in (0, 1):
            raise ValueError
        offset, sequence = 4, 0
        if flags & 1:
            sequence = struct.unpack_from('>i', frame, offset)[0]
            offset += 4
        if kind == 15:
            # Validate the error envelope, but never decode/forward its private body.
            struct.unpack_from('>I', frame, offset)
            offset += 4
        size = struct.unpack_from('>I', frame, offset)[0]
        offset += 4
        if size != len(frame) - offset:
            raise ValueError
        if kind == 15:
            raise ASRError('listening_asr_provider_error')
        if serialization != 1:
            raise ValueError
        payload = frame[offset:]
        if compression:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            payload = inflater.decompress(payload, MAX_DECOMPRESSED_BYTES + 1)
            if not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
                raise ValueError
        if len(payload) > MAX_DECOMPRESSED_BYTES:
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        if data.get('code') not in (None, 0, 1000, 20000000, '20000000'):
            raise ASRError('listening_asr_provider_error')
        return data, bool(flags & 2 or sequence < 0)
    except ASRError:
        raise
    except (ValueError, TypeError, struct.error, zlib.error, UnicodeError, RecursionError):
        raise ASRError('listening_asr_protocol') from None


def _segments(data):
    """result_type=full：存在 utterances 时替换全部快照；缺省字段是 ACK。"""
    try:
        result = data.get('result')
        if result is None:
            return None
        if isinstance(result, list):
            if not result:
                return None
            if len(result) != 1:
                raise ValueError
            result = result[0]
        if not isinstance(result, dict):
            raise ValueError
        if 'utterances' not in result:
            if result.get('text'):
                # With show_utterances requested, silently losing text is unsafe.
                raise ValueError
            return None
        utterances = result['utterances']
        if not isinstance(utterances, list) or len(utterances) > MAX_SEGMENTS:
            raise ValueError
        segments, total_chars = [], 0
        for item in utterances:
            if not isinstance(item, dict):
                raise ValueError
            text = item.get('text', '')
            start, end = item.get('start_time'), item.get('end_time')
            if 'start_time' not in item or 'end_time' not in item:
                # Real stream interim results may omit utterance start_time while
                # supplying timed words. Preserve explicit bounds; never use zero
                # or audio_info.duration as a guessed utterance boundary.
                words = item.get('words')
                if not isinstance(words, list) or not words:
                    raise ValueError
                for word in words:
                    if not isinstance(word, dict):
                        raise ValueError
                    word_start, word_end = word.get('start_time'), word.get('end_time')
                    if (
                        type(word_start) is not int
                        or type(word_end) is not int
                        or not 0 <= word_start <= word_end <= 20 * 60 * 1000
                    ):
                        raise ValueError
                if 'start_time' not in item:
                    start = min(word['start_time'] for word in words)
                if 'end_time' not in item:
                    end = max(word['end_time'] for word in words)
            if (
                not isinstance(text, str)
                or len(text) > 16_000
                or type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end <= 20 * 60 * 1000
            ):
                raise ValueError
            total_chars += len(text)
            if total_chars > MAX_TEXT_CHARS:
                raise ValueError
            additions = item.get('additions') or {}
            if isinstance(additions, str):
                additions = json.loads(additions)
            if not isinstance(additions, dict):
                raise ValueError
            speaker = item.get('speaker_id')
            if speaker is None:
                speaker = additions.get('speaker_id', additions.get('speaker'))
            if speaker is None or speaker == '':
                speaker = 'unknown'
            elif type(speaker) is int:
                speaker = str(speaker)
            if not isinstance(speaker, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', speaker):
                raise ValueError
            if not text.strip():
                continue
            segments.append(
                {
                    'id': f'segment-{len(segments) + 1}',
                    'speaker_id': speaker,
                    'text': text,
                    'start_ms': start,
                    'end_ms': end,
                }
            )
        return segments
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ASRError('listening_asr_protocol') from None


class _Upstream:
    def __init__(self, socket):
        self.socket = socket
        self.sent_final = False
        self.received_final = False
        self.closed = False
        self.total_audio_bytes = 0
        self._audio_buffer = bytearray()
        self._sending = asyncio.Lock()
        self._reading = False

    async def send_audio(self, pcm, final=False):
        """累计客户端小包到200ms；final提交余量，计数包含尚未发出的音频。"""
        if not isinstance(pcm, bytes) or len(pcm) % 2 or len(pcm) > MAX_AUDIO_FRAME_BYTES or type(final) is not bool:
            raise ValueError('invalid_listening_audio')
        async with self._sending:
            if self.closed or self.sent_final or self.received_final:
                raise ValueError('listening_asr_closed')
            if self.total_audio_bytes + len(pcm) > MAX_AUDIO_BYTES:
                raise ValueError('listening_audio_limit')
            self.total_audio_bytes += len(pcm)
            self._audio_buffer.extend(pcm)
            chunks = []
            while len(self._audio_buffer) >= AUDIO_CHUNK_BYTES:
                chunks.append(bytes(self._audio_buffer[:AUDIO_CHUNK_BYTES]))
                del self._audio_buffer[:AUDIO_CHUNK_BYTES]
            if final:
                if self._audio_buffer:
                    chunks.append(bytes(self._audio_buffer))
                    self._audio_buffer.clear()
                elif not chunks:
                    chunks = [b'']
            for index, chunk in enumerate(chunks):
                last = final and index == len(chunks) - 1
                try:
                    await self.socket.send(_encode(chunk, 2, final=last))
                except Exception:
                    raise ASRError('listening_asr_unavailable') from None
                if last:
                    self.sent_final = True

    async def events(self):
        if self._reading or self.closed:
            raise ValueError('listening_asr_closed')
        self._reading = True
        segments = []
        while not self.closed:
            try:
                frame = await self.socket.recv()
            except Exception:
                raise ASRError('listening_asr_incomplete') from None
            data, final = _decode(frame)
            self.received_final = final
            snapshot = _segments(data)
            if snapshot is not None:
                segments = snapshot
            if snapshot is not None or final:
                # Copy so a caller cannot mutate the snapshot retained for a later ACK.
                yield {'segments': [dict(segment) for segment in segments], 'final': final}
            if final:
                return

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self._audio_buffer.clear()
        with suppress(Exception):
            await asyncio.wait_for(self.socket.close(), timeout=3)


@asynccontextmanager
async def connect_asr(store):
    """固定端点，无 TTS；调用方持续读 events，并在发 final 后等待协议终包。"""
    if not _enabled(store):
        raise ASRError('listening_asr_disabled')
    fields = _credentials(store)
    if not (fields.get('api_key') or (fields.get('app_id') and fields.get('access_token'))):
        raise ASRError('listening_asr_not_configured')
    request_id = str(uuid.uuid4())
    headers = {'X-Api-Resource-Id': RESOURCE_ID, 'X-Api-Request-Id': request_id, 'X-Api-Connect-Id': request_id}
    if fields['api_key']:
        headers['X-Api-Key'] = fields['api_key']
    else:
        # SAUC legacy auth differs from realtime dialogue: App-Key is the app ID.
        headers.update({'X-Api-App-Key': fields['app_id'], 'X-Api-Access-Key': fields['access_token']})
    try:
        options = {
            'additional_headers': headers,
            'max_size': MAX_RESPONSE_BYTES,
            'max_queue': 4,
            'open_timeout': 15,
            'close_timeout': 3,
            'compression': None,
        }
        # websockets 14 connects directly; 15+ defaults to environment proxy discovery.
        if _CONNECT_SUPPORTS_PROXY:
            options['proxy'] = None
        connection = connect(ENDPOINT, **options)
        socket = await connection.__aenter__()
    except Exception:
        raise ASRError('listening_asr_unavailable') from None
    upstream = _Upstream(socket)
    try:
        request = {
            'user': {'uid': request_id},
            'audio': {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1},
            'request': {
                'model_name': 'bigmodel',
                'enable_nonstream': True,
                'enable_speaker_info': True,
                'show_utterances': True,
                'ssd_version': '200',
                'result_type': 'full',
                'enable_itn': False,
                'enable_punc': True,
                'enable_ddc': False,
            },
        }
        try:
            await socket.send(_encode(json.dumps(request).encode(), 1, json_payload=True))
        except Exception:
            raise ASRError('listening_asr_unavailable') from None
        yield upstream
    finally:
        await upstream.close()
        with suppress(Exception):
            await asyncio.wait_for(connection.__aexit__(None, None, None), timeout=3)
