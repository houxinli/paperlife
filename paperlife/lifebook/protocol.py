"""豆包端到端实时语音 v1 二进制帧。"""

import gzip
import json
import struct


def encode(event, data, session=''):
    audio = isinstance(data, bytes)
    payload = data if audio else json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    result = bytes([0x11, 0x24 if audio else 0x14, 0 if audio else 0x10, 0]) + struct.pack('>I', event)
    if session:
        sid = session.encode()
        result += struct.pack('>I', len(sid)) + sid
    return result + struct.pack('>I', len(payload)) + payload


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) < 8 or raw[0] >> 4 != 1:
        raise ValueError('Invalid provider frame')
    kind, flags, serial, compression = raw[1] >> 4, raw[1] & 15, raw[2] >> 4, raw[2] & 15
    offset = (raw[0] & 15) * 4

    def integer():
        nonlocal offset
        if offset + 4 > len(raw):
            raise ValueError('Truncated frame')
        value = struct.unpack_from('>I', raw, offset)[0]
        offset += 4
        return value

    def blob():
        nonlocal offset
        size = integer()
        if offset + size > len(raw):
            raise ValueError('Truncated payload')
        value = raw[offset : offset + size]
        offset += size
        return value

    if kind == 15:
        code = integer()
        raise ValueError(f'Provider error {code}')
    if flags & 3 in (1, 3):
        integer()
    event = integer() if flags & 4 else 0
    if event in (50, 51, 52) or event >= 100:
        blob()  # connection/session identifier
    payload = blob()
    if compression == 1:
        payload = gzip.decompress(payload)
    elif compression != 0:
        raise ValueError('Unsupported compression')
    return {'event': event, 'data': json.loads(payload) if serial == 1 and payload else payload}
