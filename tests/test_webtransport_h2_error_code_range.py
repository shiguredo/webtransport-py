"""WebTransport over HTTP/2 の error_code 範囲検証テスト

draft-15 Section 6.2 / 6.3 の MUST 「Application Protocol Error Code が
0xffffffff を超えたら WT_ERROR セッションエラー」を検証する。 0x52 は
WT_ERROR (draft-15 Section 3.4 の 0xTBD) のプレースホルダ。 draft で値が
確定したら更新する。 0xffffffff ちょうどは合法で、従来どおり
StreamReset / StopSending が届く。終端状態や二重受信と同時に立っても
範囲検証が先で、 0x51 ではなく 0x52 になる。
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
_MAX_ERROR_CODE = 0xFFFFFFFF
_OVER_MAX_ERROR_CODE = 0x100000000
_MSG_RESET = "WT_RESET_STREAM error code exceeds 0xffffffff"
_MSG_STOP = "WT_STOP_SENDING error code exceeds 0xffffffff"


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_wt_reset_stream_capsule(stream_id: int, error_code: int, reliable_size: int) -> bytes:
    """WT_RESET_STREAM capsule のワイヤバイト列を組み立てる

    Error Code は varint のため、 0xffffffff 超の注入にも使う。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code) + _encode_varint(reliable_size)
    return _encode_capsule(0x190B4D39, payload)


def _encode_wt_stop_sending_capsule(stream_id: int, error_code: int) -> bytes:
    """WT_STOP_SENDING capsule のワイヤバイト列を組み立てる

    Error Code は varint のため、 0xffffffff 超の注入にも使う。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code)
    return _encode_capsule(0x190B4D3A, payload)


def _encode_wt_stream_fin_capsule(stream_id: int, data: bytes) -> bytes:
    """WT_STREAM_FIN capsule のワイヤバイト列を組み立てる

    DataRecvd にしたストリームへ範囲外の WT_RESET_STREAM を注入するために使う。
    """
    return _encode_capsule(0x190B4D3B, _encode_varint(stream_id) + data)


def _encode_wt_close_session_capsule(error_code: int, error_message: str) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる"""
    return _encode_capsule(0x2843, error_code.to_bytes(4, "big") + error_message.encode("utf-8"))


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
    expected = _encode_wt_close_session_capsule(_WT_ERROR, error_message)
    assert expected in wire


def _assert_no_wt_error_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認

    0x68 0x43 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint。
    エラー検知 (close_session 呼び出し) があれば必ずワイヤに現れるため、
    Type の非存在でエラー送出なしを検証できる。
    """
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def test_wt_reset_stream_error_code_over_max_sends_wt_error() -> None:
    """0xffffffff 超の error_code を含む WT_RESET_STREAM で WT_ERROR になることを確認

    修正前は uint32_t へ切り詰めて StreamReset に渡し、セッションエラーに
    しなかった。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(
            session_id,
            _encode_wt_reset_stream_capsule(0, _OVER_MAX_ERROR_CODE, 0),
        )
    )
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_RESET
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)
    _assert_wt_error_sent(server, _MSG_RESET)


def test_wt_reset_stream_over_max_unknown_nonzero_sends_wt_error() -> None:
    """未知ストリームかつ Reliable Size > 0 でも 0xffffffff 超なら 0x52 になることを確認

    範囲検証は未知ストリームの non-zero reliable size (0x51) より先。
    順序が入れ替わると 0x51 になる。既存の over-max テストは
    Reliable Size 0 のため、この経路を固定できない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(
            session_id,
            _encode_wt_reset_stream_capsule(0, _OVER_MAX_ERROR_CODE, 5),
        )
    )
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_RESET
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)
    _assert_wt_error_sent(server, _MSG_RESET)


def test_wt_stop_sending_error_code_over_max_sends_wt_error() -> None:
    """0xffffffff 超の error_code を含む WT_STOP_SENDING で WT_ERROR になることを確認

    修正前は uint32_t へ切り詰めて StopSending に渡し、セッションエラーに
    しなかった。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, _OVER_MAX_ERROR_CODE))
    )
    assert ret > 0, "WT_STOP_SENDING カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_STOP
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.STOP_SENDING for event in events)
    _assert_wt_error_sent(server, _MSG_STOP)


def test_wt_reset_stream_over_max_on_terminal_stream_sends_wt_error() -> None:
    """終端状態のストリームへ 0xffffffff 超の WT_RESET_STREAM を送ると 0x52 になることを確認

    範囲検証は終端状態 (0x51) より先。順序が入れ替わると 0x51 になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_fin_capsule(0, b"fin")))
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"
    _drain_events(server)

    ret = server.receive(
        _encode_data_frame(
            session_id,
            _encode_wt_reset_stream_capsule(0, _OVER_MAX_ERROR_CODE, 3),
        )
    )
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_RESET
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)
    _assert_wt_error_sent(server, _MSG_RESET)


def test_wt_stop_sending_second_over_max_sends_wt_error() -> None:
    """2 回目の WT_STOP_SENDING が 0xffffffff 超なら 0x52 になることを確認

    範囲検証は二重受信 (0x51) より先。順序が入れ替わると 0x51 になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, 42)))
    assert ret > 0, "1 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert len(stop_events) == 1

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, _OVER_MAX_ERROR_CODE))
    )
    assert ret > 0, "2 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert error_events[0].error_message == _MSG_STOP
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.STOP_SENDING for event in events)
    _assert_wt_error_sent(server, _MSG_STOP)


def test_wt_reset_stream_error_code_max_is_accepted() -> None:
    """0xffffffff ちょうどの error_code は StreamReset として届くことを確認

    未知ストリームかつ Reliable Size 0 なら、上限ちょうどは従来どおり
    StreamReset を push し、セッションは閉じない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, _MAX_ERROR_CODE, 0))
    )
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    reset_events = [event for event in events if event.type == h2.EventType.STREAM_RESET]
    assert len(reset_events) == 1
    assert reset_events[0].error_code == _MAX_ERROR_CODE
    assert reset_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_wt_error_sent(server)


def test_wt_stop_sending_error_code_max_is_accepted() -> None:
    """0xffffffff ちょうどの error_code は StopSending として届くことを確認

    1 回目の WT_STOP_SENDING は従来どおりイベントを push し、セッションは
    閉じない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, _MAX_ERROR_CODE))
    )
    assert ret > 0, "WT_STOP_SENDING カプセルの注入に失敗しました"
    events = _drain_events(server)
    stop_events = [event for event in events if event.type == h2.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].error_code == _MAX_ERROR_CODE
    assert stop_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_wt_error_sent(server)
