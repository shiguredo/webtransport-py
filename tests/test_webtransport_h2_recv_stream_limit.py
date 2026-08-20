"""WebTransport over HTTP/2 の受信ストリーム数上限テスト

draft-15 Section 6.7 の MUST 「広告した Maximum Streams を超えるストリーム
受信は WT_FLOW_CONTROL_ERROR でセッションを閉じる」を検証する。 0x50 は
WT_FLOW_CONTROL_ERROR (draft-15 Section 3.4 の 0xTBD) のプレースホルダ。
同一タイプ・方向の低い ID も暗黙オープンとして累積カウントし、閉じた
ストリームも含める。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _drain_events,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

_WT_FLOW_CONTROL_ERROR = 0x50
_MSG_STREAM_LIMIT = "peer exceeded Maximum Streams limit"


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_wt_stream_capsule(stream_id: int, data: bytes, fin: bool = False) -> bytes:
    """WT_STREAM / WT_STREAM_FIN capsule のワイヤバイト列を組み立てる"""
    capsule_type = 0x190B4D3B if fin else 0x190B4D3C
    return _encode_capsule(capsule_type, _encode_varint(stream_id) + data)


def _encode_wt_reset_stream_capsule(stream_id: int, error_code: int, reliable_size: int) -> bytes:
    """WT_RESET_STREAM capsule のワイヤバイト列を組み立てる"""
    payload = _encode_varint(stream_id) + _encode_varint(error_code) + _encode_varint(reliable_size)
    return _encode_capsule(0x190B4D39, payload)


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


def _assert_flow_control_error_sent(server: h2.Session) -> None:
    """WT_FLOW_CONTROL_ERROR (0x50) の WT_CLOSE_SESSION が送出されることを確認"""
    wire = server.send()
    assert wire is not None
    expected = _encode_wt_close_session_capsule(_WT_FLOW_CONTROL_ERROR, _MSG_STREAM_LIMIT)
    assert expected in wire


def _assert_no_close_session_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認

    0x68 0x43 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint。
    """
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def _create_server_with_stream_limits(
    max_streams_bidi: int, max_streams_uni: int
) -> tuple[h2.Session, h2.Session]:
    """サーバーの受信ストリーム数上限を指定したセッションペアを作成する

    wt_initial_max_streams_* が SETTINGS / WT_MAX_STREAMS で広告する値になり、
    max_streams_*_remote として受信検証に使われる。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.wt_initial_max_streams_bidi = max_streams_bidi
    server_config.wt_initial_max_streams_uni = max_streams_uni
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    return client, server


def _inject_wt_stream(
    server: h2.Session, session_id: int, stream_id: int, data: bytes, fin: bool = False
) -> None:
    """サーバーへ WT_STREAM カプセルを DATA フレームとして注入する"""
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(stream_id, data, fin))
    )
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"


def test_wt_stream_at_max_streams_is_accepted() -> None:
    """広告した Maximum Streams ちょうどの受信はセッションエラーにならないことを確認

    上限 2 に対するクライアント起点双方向の 2 本目 (ID 4) は累積 2 で合法。
    生の stream_id と上限を比較する誤実装だと 4 > 2 で誤って閉じる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=2, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 4, b"ok")
    events = _drain_events(server)
    stream_events = [event for event in events if event.type == h2.EventType.STREAM_DATA]
    assert len(stream_events) == 1
    assert stream_events[0].stream_id == 4
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_close_session_sent(server)


def test_wt_stream_uni_at_max_streams_is_accepted() -> None:
    """単方向の Maximum Streams ちょうどもセッションエラーにならないことを確認

    上限 1 に対するクライアント起点単方向の 1 本目 (ID 2) は累積 1 で合法。
    生の stream_id と比較する誤実装だと 2 > 1 で誤って閉じる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=100, max_streams_uni=1)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 2, b"ok")
    events = _drain_events(server)
    stream_events = [event for event in events if event.type == h2.EventType.STREAM_DATA]
    assert len(stream_events) == 1
    assert stream_events[0].stream_id == 2
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_close_session_sent(server)


def test_wt_stream_existing_id_does_not_exceed_limit() -> None:
    """既存ストリームへの再受信は Maximum Streams 超過にならないことを確認

    上限ちょうどで受理した ID への 2 通目は暗黙作成経路を通らない。
    制限チェックを lookup の前に置くと 2 通目で誤って 0x50 になる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=2, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 4, b"ok")
    _drain_events(server)

    _inject_wt_stream(server, session_id, 4, b"again")
    events = _drain_events(server)
    stream_events = [event for event in events if event.type == h2.EventType.STREAM_DATA]
    assert len(stream_events) == 1
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_no_close_session_sent(server)


def test_wt_stream_over_max_streams_sends_flow_control_error() -> None:
    """高い ID の受信で低い ID もカウントされ Maximum Streams を超えることを確認

    上限 1 に対してクライアント起点双方向の 2 本目 (ID 4) を先に送る。
    ID 0 も暗黙オープンされ累積 2 になり、修正前は無制限にエントリを作った。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 4, b"over")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _MSG_STREAM_LIMIT
    assert error_events[0].session_id == session_id
    assert error_events[0].stream_id == 4
    assert all(event.type != h2.EventType.STREAM_DATA for event in events)
    _assert_flow_control_error_sent(server)


def test_wt_stream_empty_fin_over_max_sends_flow_control_error() -> None:
    """空の WT_STREAM_FIN でも未知ストリームなら上限超過を検知することを確認

    Stream ID だけのカプセルは length == 0 ではない。データを含まない
    オープンも Maximum Streams の対象。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 4, b"", fin=True)
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _MSG_STREAM_LIMIT
    assert all(event.type != h2.EventType.STREAM_DATA for event in events)
    _assert_flow_control_error_sent(server)


def test_wt_stream_over_max_after_closed_sends_flow_control_error() -> None:
    """閉じたストリームも累積カウントし、上限超過で 0x50 になることを確認

    上限 1 で ID 0 を FIN したあと ID 4 を送る。閉じた ID 0 を含め累積 2。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"fin", fin=True)
    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events)

    _inject_wt_stream(server, session_id, 4, b"over")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _MSG_STREAM_LIMIT
    _assert_flow_control_error_sent(server)


def test_wt_reset_stream_over_max_sends_flow_control_error() -> None:
    """未知ストリームの WT_RESET_STREAM でも Maximum Streams 超過を検知することを確認

    Reliable Size 0 の暗黙作成は制限の対象。超過時は StreamReset を push
    せず 0x50 でセッションを閉じる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(4, 0, 0)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _MSG_STREAM_LIMIT
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)
    _assert_flow_control_error_sent(server)


def test_wt_stream_uni_over_max_sends_flow_control_error() -> None:
    """単方向の Maximum Streams 超過も 0x50 になることを確認

    クライアント起点単方向は ID 2, 6, ... 。上限 1 に対して ID 6 は累積 2。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=100, max_streams_uni=1)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 6, b"over")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _MSG_STREAM_LIMIT
    _assert_flow_control_error_sent(server)


def test_wt_reset_stream_over_max_error_code_sends_wt_error() -> None:
    """ストリーム数超過と同時に error_code が範囲外なら 0x52 が先になることを確認

    順序が入れ替わると 0x50 になる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_reset_stream_capsule(4, 0x100000000, 0))
    )
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x52
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)


def test_wt_reset_stream_over_max_nonzero_reliable_size_sends_state_error() -> None:
    """ストリーム数超過と同時に未知ストリームの Reliable Size > 0 なら 0x51 が先になることを確認

    順序が入れ替わると 0x50 になる。
    """
    client, server = _create_server_with_stream_limits(max_streams_bidi=1, max_streams_uni=100)
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(4, 0, 5)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51
    assert error_events[0].error_message == (
        "WT_RESET_STREAM non-zero reliable size, unknown stream"
    )
    assert all(event.type != h2.EventType.STREAM_RESET for event in events)
