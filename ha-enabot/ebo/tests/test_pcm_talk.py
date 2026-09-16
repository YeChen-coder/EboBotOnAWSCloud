import json

from pcm_talk import FRAME_BYTES, PcmStream, PcmTalkServer


def test_24khz_pcm_is_resampled_into_8khz_20ms_frames():
    stream = PcmStream("stream-test")
    stream.push(b"\x01\x00" * 960)  # 40 ms at 24 kHz
    stream.finish_input()

    first = stream.pop_frame()
    second = stream.pop_frame()
    assert first is not None and len(first) == FRAME_BYTES
    assert second is not None and len(second) == FRAME_BYTES
    assert stream.pop_frame() is None
    assert stream.drained()


def test_played_ms_counts_only_frames_actually_sent():
    stream = PcmStream("stream-test")
    assert stream.played_ms == 0
    stream.mark_played()
    stream.mark_played()
    assert stream.played_ms == 40


def test_stream_authentication_requires_token_format_rate_and_node():
    server = PcmTalkServer("127.0.0.1", 0, "secret", "ebo", lambda _s: None, lambda _s: None)
    valid = json.dumps(
        {
            "type": "start",
            "token": "secret",
            "node": "ebo",
            "stream_id": "one",
            "rate": 24000,
            "channels": 1,
            "format": "pcm16",
        }
    )
    assert server._authenticate(valid)[0]
    assert not server._authenticate(valid.replace("secret", "wrong"))[0]
    assert not server._authenticate(valid.replace("24000", "16000"))[0]
    assert not server._authenticate(valid.replace('"ebo"', '"other"'))[0]


def test_replacing_a_stream_marks_the_old_one_stopped():
    old = PcmStream("old")
    old.push(b"\0\0" * 480)
    old.stop("replaced")
    assert old.stopped.is_set()
    assert old.stop_reason == "replaced"
    assert old.pop_frame() is None
