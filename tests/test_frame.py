from glm.protocol.constants import CommStatus, FrameFormat, FrameType
from glm.protocol.frame import Frame, FrameDecoder, encode


def test_encode_known_outgoing_request():
    """The hand-derived autosync-on frame from the original main.py."""
    f = Frame.request(cmd=0x55, payload=bytes([0x01, 0x00]),
                      req_fmt=FrameFormat.LONG, resp_fmt=FrameFormat.LONG)
    assert encode(f) == bytes.fromhex("c055020100" + "1a")


def test_decode_request_roundtrip():
    f = Frame.request(cmd=0x55, payload=bytes([0x01, 0x00]))
    decoder = FrameDecoder()
    out = list(decoder.feed(encode(f)))
    assert len(out) == 1
    assert out[0].type == FrameType.REQUEST
    assert out[0].cmd == 0x55
    assert out[0].payload == bytes([0x01, 0x00])


def test_decode_handles_split_feeds():
    """Bytes may arrive across multiple GATT notification chunks."""
    raw = encode(Frame.request(cmd=0x55, payload=bytes([0x01, 0x00])))
    decoder = FrameDecoder()
    assert list(decoder.feed(raw[:3])) == []
    out = list(decoder.feed(raw[3:]))
    assert len(out) == 1 and out[0].cmd == 0x55


def test_decode_two_frames_in_one_buffer():
    raw = encode(Frame.request(cmd=0x55, payload=bytes([0x01, 0x00])))
    decoder = FrameDecoder()
    out = list(decoder.feed(raw + raw))
    assert len(out) == 2


def test_decode_short_format():
    f = Frame.request(cmd=0x53, req_fmt=FrameFormat.SHORT, resp_fmt=FrameFormat.LONG)
    assert len(encode(f)) == 3
    out = list(FrameDecoder().feed(encode(f)))
    assert len(out) == 1 and out[0].payload == b""


def test_decode_ext_format_roundtrip():
    payload = bytes(range(50))
    f = Frame.request(cmd=0x90, payload=payload,
                      req_fmt=FrameFormat.EXT, resp_fmt=FrameFormat.EXT)
    out = list(FrameDecoder().feed(encode(f)))
    assert len(out) == 1 and out[0].payload == payload


def test_decode_response_long():
    f = Frame(type=FrameType.RESPONSE, cmd=0, payload=bytes([0xAB, 0xCD]),
              status=CommStatus.SUCCESS, req_fmt=FrameFormat.LONG)
    out = list(FrameDecoder().feed(encode(f)))
    assert len(out) == 1
    assert out[0].type == FrameType.RESPONSE
    assert out[0].status == CommStatus.SUCCESS
    assert out[0].payload == bytes([0xAB, 0xCD])
