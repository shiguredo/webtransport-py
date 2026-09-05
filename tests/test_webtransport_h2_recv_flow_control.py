"""WebTransport over HTTP/2 の受信フロー制御違反テスト

draft-15 Section 6.5 / 6.6 の MUST 「受信データが広告した WT_MAX_DATA /
WT_MAX_STREAM_DATA を超えたら WT_FLOW_CONTROL_ERROR でセッションを閉じる」
を検証する。不正な超過データはワイヤ注入で再現する (公開 API の
send_stream_data は送信側クレジットで塞がれ、超過分を送れないため)。
セッション閉鎖は close_session 経由の WT_CLOSE_SESSION (error code 0x50)
で実現され、あわせて Error イベント (0x50) を push する。0x50 は
WT_FLOW_CONTROL_ERROR (0xTBD) のプレースホルダ (draft-15 Section 3.4)。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

_WT_STREAM = 0x190B4D3C
_PEER_EXCEEDED = "peer exceeded flow control limit"


def _encode_wt_stream_capsule(stream_id: int, data: bytes) -> bytes:
    """WT_STREAM capsule (FIN なし) のワイヤバイト列を組み立てる"""
    return _encode_capsule(_WT_STREAM, _encode_varint(stream_id) + data)


def _encode_wt_close_session_capsule(error_code: int, error_message: str) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる"""
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    return _encode_capsule(0x2843, payload)


def _assert_flow_control_error_sent(server: h2.Session) -> None:
    """WT_FLOW_CONTROL_ERROR (0x50) の WT_CLOSE_SESSION が送出されることを確認

    0x50 は draft-15 Section 3.4 の 0xTBD のプレースホルダ。draft で値が
    確定したら更新する。
    """
    wire = server.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0x50, _PEER_EXCEEDED) in wire


def _assert_no_close_session_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認"""
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def _inject_wt_stream(server: h2.Session, session_id: int, stream_id: int, data: bytes) -> None:
    """サーバーへ WT_STREAM カプセルを DATA フレームとして注入する"""
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(stream_id, data)))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"


def _create_server_with_recv_limits(
    max_data: int, max_stream_data: int
) -> tuple[h2.Session, h2.Session]:
    """サーバーの受信上限を指定したセッションペアを作成する

    wt_initial_max_data がセッション受信上限 (max_data_remote)、
    wt_initial_max_stream_data がストリーム受信上限 (max_stream_data_remote)
    になる。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.wt_initial_max_data = max_data
    server_config.wt_initial_max_stream_data = max_stream_data
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    return client, server


def test_wt_stream_exceeds_max_stream_data_closes_session() -> None:
    """WT_MAX_STREAM_DATA 超過で WT_FLOW_CONTROL_ERROR になることを確認

    ストリーム受信上限 4 バイトに対して 5 バイトを注入する。修正前は Error
    イベントを push するだけでセッションは閉じず、超過データは捨てられて
    ピアは送り続けられた。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)


def test_wt_stream_exceeds_max_data_closes_session() -> None:
    """WT_MAX_DATA 超過で WT_FLOW_CONTROL_ERROR になることを確認

    セッション受信上限 4 バイト・ストリーム上限 100 バイトに対して 5 バイト
    を注入し、セッション上限側の超過経路を検証する。
    """
    client, server = _create_server_with_recv_limits(max_data=4, max_stream_data=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)


def test_wt_stream_exceeds_flow_control_pushes_error_event() -> None:
    """受信超過は Error イベント (0x50) を push したうえでセッションを閉じることを確認

    カプセル値減少の検知 (Error を push しない) とは経路を分け、受信超過は
    高レベル層の on_error 通知のために Error イベントを残す。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x50
    assert error_events[0].error_message == _PEER_EXCEEDED
    assert error_events[0].session_id == session_id
    assert error_events[0].stream_id == 0
    stream_events = [event for event in events if event.type == h2.EventType.STREAM_DATA]
    assert stream_events == []
    _assert_flow_control_error_sent(server)


def test_wt_stream_cumulative_exceeds_max_stream_data_closes_session() -> None:
    """複数 WT_STREAM の累積がストリーム受信上限を超えたら閉じることを確認

    上限 4 バイトに対して 3 バイトのあと 2 バイトを送り、2 回目で超過する。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"123")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"123"
    _assert_no_close_session_sent(server)

    _inject_wt_stream(server, session_id, 0, b"45")
    _assert_flow_control_error_sent(server)


def test_wt_stream_cumulative_exceeds_max_data_closes_session() -> None:
    """複数 WT_STREAM の累積がセッション受信上限を超えたら閉じることを確認

    セッション上限 4 バイト・ストリーム上限 100 バイトに対して 3 バイトの
    あと 2 バイトを送り、2 回目でセッション上限を超える。
    """
    client, server = _create_server_with_recv_limits(max_data=4, max_stream_data=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"123")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"123"
    _assert_no_close_session_sent(server)

    _inject_wt_stream(server, session_id, 0, b"45")
    _assert_flow_control_error_sent(server)


def test_wt_stream_within_recv_limit_does_not_close() -> None:
    """受信上限ちょうどの WT_STREAM はセッションを閉じないことを確認

    上限 4 バイトに対して 4 バイトは超過ではない。StreamData が届き、
    WT_CLOSE_SESSION は送出されない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"1234")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"1234"
    _assert_no_close_session_sent(server)


def test_wt_stream_exceeds_flow_control_ignores_following_capsules() -> None:
    """受信超過の検知後、同一 receive() 内の後続カプセルが処理されないことを確認

    close_session が is_terminated を立て、process_capsules が後続を捨てる。
    超過の直後に別ストリームの WT_STREAM を連結しても StreamData は届かない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    capsules = _encode_wt_stream_capsule(0, b"12345") + _encode_wt_stream_capsule(4, b"x")
    ret = server.receive(_encode_data_frame(session_id, capsules))
    assert ret > 0, "連結カプセルの注入に失敗しました"
    events = _drain_events(server)
    assert all(event.stream_id != 4 for event in events)
    assert all(event.type != h2.EventType.STREAM_DATA for event in events)
    _assert_flow_control_error_sent(server)


def test_peer_cannot_continue_sending_after_recv_flow_control_error() -> None:
    """受信超過で閉じたあと、後続の receive() では超過データが届かないことを確認

    修正前はセッションが開いたままだったため、次の WT_STREAM も受信処理に
    入った (超過分は捨てられるだけだった)。閉じたあとは is_established が
    落ち、新規 DATA は process_capsules に渡らない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)

    _inject_wt_stream(server, session_id, 0, b"y")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert stream_events == []
