"""WebTransport over HTTP/2 の WT_CLOSE_SESSION メッセージ検証テスト

draft-15 Section 6.12 の MUST 「Application Error Message が 1024 バイト超
または不正な UTF-8 なら WT_ERROR セッションエラー」を検証する。 0x52 は
WT_ERROR (draft-15 Section 3.4 の 0xTBD) のプレースホルダ。 draft で値が
確定したら更新する。不正メッセージは close_session へ渡さず、固定の英語
メッセージを Error イベントと WT_CLOSE_SESSION の両方に使う。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_varint,
)

from webtransport import h2

_WT_ERROR = 0x52
_MSG_TOO_LONG = "WT_CLOSE_SESSION message exceeds 1024 bytes"
_MSG_BAD_UTF8 = "WT_CLOSE_SESSION message is not valid UTF-8"


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_wt_close_session_capsule(error_code: int, message: bytes) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる

    メッセージは bytes のまま載せる。不正 UTF-8 と 1024 バイト超の注入に使う。
    """
    return _encode_capsule(0x2843, error_code.to_bytes(4, "big") + message)


def _encode_data_frame(session_id: int, payload: bytes) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる"""
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, 0x00])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def _assert_wt_error_sent(server: h2.Session, error_message: str) -> None:
    """WT_ERROR (0x52) の WT_CLOSE_SESSION が送出されることを確認

    エラー検知は close_session (WT_CLOSE_SESSION 送出 + END_STREAM) で実現
    される (draft-15 Section 3.4)。ワイヤ部分列チェックで送出を検証する。
    """
    wire = server.send()
    assert wire is not None
    expected = _encode_wt_close_session_capsule(_WT_ERROR, error_message.encode("utf-8"))
    assert expected in wire


def _assert_no_wt_error_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認

    0x68 0x43 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint。
    エラー検知 (close_session 呼び出し) があれば必ずワイヤに現れるため、
    Type の非存在でエラー送出なしを検証できる。正常受信時の応答は
    END_STREAM のみで、 WT_CLOSE_SESSION カプセルは送らない。
    """
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def _inject_close_session(server: h2.Session, session_id: int, message: bytes) -> None:
    """サーバーへ WT_CLOSE_SESSION カプセルを DATA フレームとして注入する"""
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_close_session_capsule(0, message))
    )
    assert ret > 0, "WT_CLOSE_SESSION カプセルの注入に失敗しました"


def test_wt_close_session_message_over_1024_sends_wt_error() -> None:
    """1024 バイト超のメッセージで WT_ERROR になることを確認

    修正前は無検証で SessionClosed に渡し、セッションエラーにしなかった。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"a" * 1025)
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_TOO_LONG
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_TOO_LONG)


def test_wt_close_session_invalid_utf8_sends_wt_error() -> None:
    """不正な UTF-8 のメッセージで WT_ERROR になることを確認

    受信した不正バイト列は Error / WT_CLOSE_SESSION に載せない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"\xff")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_BAD_UTF8
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_BAD_UTF8)


def test_wt_close_session_overlong_utf8_sends_wt_error() -> None:
    """overlong 符号化の UTF-8 で WT_ERROR になることを確認

    U+0000 の overlong 2 バイト (0xC0 0x80) は RFC 3629 では不正。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"\xc0\x80")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_BAD_UTF8
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_BAD_UTF8)


def test_wt_close_session_message_exactly_1024_is_accepted() -> None:
    """1024 バイトちょうど・正しい UTF-8 はセッションエラーにならないことを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    message = b"a" * 1024
    _inject_close_session(server, session_id, message)
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 0
    assert closed_events[0].error_message == "a" * 1024
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_wt_error_sent(server)


def test_wt_close_session_valid_utf8_is_accepted() -> None:
    """短い正しい UTF-8 メッセージは SessionClosed にそのまま届くことを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, "終了".encode())
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].error_message == "終了"
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_wt_error_sent(server)
