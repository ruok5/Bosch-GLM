"""MtProtocol frame encode/decode."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .constants import CommStatus, FrameFormat, FrameType, frame_mode, split_mode
from .crc import crc8, crc16


@dataclass
class Frame:
    type: FrameType
    cmd: int
    payload: bytes = b""
    status: CommStatus = CommStatus.SUCCESS  # responses only
    req_fmt: FrameFormat = FrameFormat.LONG  # requests: chosen format; responses: format used
    resp_fmt: FrameFormat = FrameFormat.LONG  # requests only

    @classmethod
    def request(cls, cmd: int, payload: bytes = b"",
                req_fmt: FrameFormat = FrameFormat.LONG,
                resp_fmt: FrameFormat = FrameFormat.LONG) -> "Frame":
        return cls(type=FrameType.REQUEST, cmd=cmd, payload=payload,
                   req_fmt=req_fmt, resp_fmt=resp_fmt)


def encode(frame: Frame) -> bytes:
    if frame.type == FrameType.REQUEST:
        head = bytes([frame_mode(frame.req_fmt, frame.resp_fmt), frame.cmd])
        return _wrap(head, frame.payload, frame.req_fmt)
    head = bytes([frame.status])
    if frame.req_fmt == FrameFormat.EXT:
        # EXT response uniquely re-emits the cmd byte after status
        head += bytes([frame.cmd])
    return _wrap(head, frame.payload, frame.req_fmt)


def _wrap(head: bytes, payload: bytes, fmt: FrameFormat) -> bytes:
    if fmt == FrameFormat.SHORT:
        body = head
        return body + bytes([crc8(body)])
    if fmt == FrameFormat.LONG:
        body = head + bytes([len(payload)]) + payload
        return body + bytes([crc8(body)])
    if fmt == FrameFormat.EXT:
        n = len(payload)
        body = head + bytes([n & 0xFF, (n >> 8) & 0xFF]) + payload
        c = crc16(body)
        return body + bytes([c & 0xFF, (c >> 8) & 0xFF])
    raise ValueError(f"unknown frame format: {fmt}")


class FrameDecoder:
    """Incremental decoder. Feed bytes; yields completed Frames.

    Distinguishes request vs response by first byte: mode bytes have the upper
    nibble set (0xC0–0xCB); status bytes are 0x00–0x06.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> Iterator[Frame]:
        self._buf.extend(data)
        while True:
            frame, consumed = self._try_parse()
            if frame is None:
                return
            del self._buf[:consumed]
            yield frame

    def _try_parse(self) -> tuple[Frame | None, int]:
        if not self._buf:
            return None, 0
        first = self._buf[0]
        is_request = (first & 0xF0) == 0xC0
        return self._parse_request() if is_request else self._parse_response()

    def _parse_request(self) -> tuple[Frame | None, int]:
        if len(self._buf) < 2:
            return None, 0
        mode = self._buf[0]
        cmd = self._buf[1]
        req_fmt, resp_fmt = split_mode(mode)
        return self._parse_body(
            head_len=2,
            fmt=req_fmt,
            build=lambda payload: Frame(
                type=FrameType.REQUEST, cmd=cmd, payload=payload,
                req_fmt=req_fmt, resp_fmt=resp_fmt),
        )

    def _parse_response(self) -> tuple[Frame | None, int]:
        # Without the original request context we can't infer the response
        # format, so we conservatively try LONG (the common case). Callers that
        # need EXT-format responses can use a stateful decoder pair.
        if len(self._buf) < 1:
            return None, 0
        status = CommStatus(self._buf[0])
        return self._parse_body(
            head_len=1,
            fmt=FrameFormat.LONG,
            build=lambda payload: Frame(
                type=FrameType.RESPONSE, cmd=0, payload=payload, status=status),
        )

    def _parse_body(self, head_len: int, fmt: FrameFormat, build):
        buf = self._buf
        if fmt == FrameFormat.SHORT:
            total = head_len + 1
            if len(buf) < total:
                return None, 0
            body = bytes(buf[:head_len])
            if crc8(body) != buf[head_len]:
                # Re-sync: drop one byte and retry
                del buf[:1]
                return self._try_parse()
            return build(b""), total
        if fmt == FrameFormat.LONG:
            if len(buf) < head_len + 1:
                return None, 0
            n = buf[head_len]
            total = head_len + 1 + n + 1
            if len(buf) < total:
                return None, 0
            body = bytes(buf[: head_len + 1 + n])
            if crc8(body) != buf[head_len + 1 + n]:
                del buf[:1]
                return self._try_parse()
            return build(bytes(buf[head_len + 1 : head_len + 1 + n])), total
        if fmt == FrameFormat.EXT:
            if len(buf) < head_len + 2:
                return None, 0
            n = buf[head_len] | (buf[head_len + 1] << 8)
            total = head_len + 2 + n + 2
            if len(buf) < total:
                return None, 0
            body = bytes(buf[: head_len + 2 + n])
            expected = crc16(body)
            actual = buf[head_len + 2 + n] | (buf[head_len + 2 + n + 1] << 8)
            if expected != actual:
                del buf[:1]
                return self._try_parse()
            return build(bytes(buf[head_len + 2 : head_len + 2 + n])), total
        raise ValueError(f"unknown frame format: {fmt}")
