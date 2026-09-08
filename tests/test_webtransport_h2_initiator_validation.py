"""WebTransport over HTTP/2 のストリーム ID 方向検証テスト

QUIC 互換ストリーム ID (Bit 0 = initiator、Bit 1 = 方向) の検証を
カプセル種別の向きごとに行うことを検証する (draft-15 Section 5.2)。
双方向 (%4==0/1) は両方向とも可。単方向は送信者→受信者方向が
ピア initiator のみ可、受信者→送信者方向が自側 initiator のみ可。
ワイヤ注入で再現する (公開 API では非準拠な ID を送出する手段が
存在しないため)。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

_WT_STREAM = 0x190B4D3C
_WT_STOP_SENDING = 0x190B4D3A
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_RESET_STREAM = 0x190B4D39
_WT_STREAM_STATE_ERROR = 0x51


def _inject(server: h2.Session, session_id: int, capsule: bytes) -> None:
    """カプセルをワイヤ注入する"""
    assert server.receive(_encode_data_frame(session_id, capsule)) > 0


def _stream_state_errors(server: h2.Session) -> list:
    """WT_STREAM_STATE_ERROR イベントを取り出す"""
    return [
        event
        for event in _drain_events(server)
        if event.type == h2.EventType.ERROR and event.error_code == _WT_STREAM_STATE_ERROR
    ]


def test_stream_to_self_send_only_rejected() -> None:
    """自側送信専用の未作成 ID への WT_STREAM 受信が拒否されることを確認

    サーバー視点の ID 3 (サーバー起点単方向) は自側送信専用のため、
    受信すると WT_STREAM_STATE_ERROR になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    payload = _encode_varint(3) + b"bad"
    _inject(server, session_id, _encode_capsule(_WT_STREAM, payload))

    assert len(_stream_state_errors(server)) == 1
    assert server.get_session_ids() == []


def test_spoofed_bidi_does_not_clobber_open() -> None:
    """自側 initiator の双方向 ID 偽装で open_stream が上書きしないことを確認

    サーバー視点の ID 1 (サーバー起点双方向) への WT_STREAM は双方向の
    ため受理されるが、後続の open_stream は同 ID を払い出さず -1 を返す
    (カウンタも消費しないため次回も -1 になる)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    payload = _encode_varint(1) + b"spoof"
    _inject(server, session_id, _encode_capsule(_WT_STREAM, payload))
    assert not _stream_state_errors(server)
    assert 1 in server.get_stream_ids(session_id)

    # 同 ID の払い出しは繰り返し拒否される (カウンタを消費しないため)
    assert server.open_stream(session_id, False) == -1
    assert server.open_stream(session_id, False) == -1
    assert 1 in server.get_stream_ids(session_id)
    assert server.get_session_ids() == [session_id]


def test_data_to_send_only_stream_rejected() -> None:
    """自側送信専用の既存ストリームへの DATA 受信が拒否されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # サーバーが単方向ストリーム (ID 3) を開く
    stream_id = server.open_stream(session_id, True)
    assert stream_id == 3

    # 同 ID への DATA 受信は拒否される
    payload = _encode_varint(3) + b"bad"
    _inject(server, session_id, _encode_capsule(_WT_STREAM, payload))

    assert len(_stream_state_errors(server)) == 1
    assert server.get_session_ids() == []


def test_send_to_receive_only_stream_ignored() -> None:
    """受信専用ストリームへの送信が黙って無視されることを確認

    クライアント起点単方向 (ID 2) はサーバーから見て受信専用のため、
    send_stream_data は送出せずセッションも閉じない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = client.open_stream(session_id, True)
    assert stream_id == 2
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)
    assert stream_id in server.get_stream_ids(session_id)

    # 受信専用への送信は無視される
    server.send_stream_data(session_id, stream_id, b"should-drop", False)
    assert server.send() is None
    assert server.get_session_ids() == [session_id]
    assert client.get_session_ids() == [session_id]


def test_stop_sending_to_receive_only_rejected() -> None:
    """自側受信専用への WT_STOP_SENDING 受信が拒否されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = client.open_stream(session_id, True)
    assert stream_id == 2
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)

    # ID 2 (ピア起点単方向 = 自側受信専用) への STOP_SENDING は拒否される
    payload = _encode_varint(2) + _encode_varint(0)
    _inject(server, session_id, _encode_capsule(_WT_STOP_SENDING, payload))

    assert len(_stream_state_errors(server)) == 1
    assert server.get_session_ids() == []


def test_stop_sending_to_send_only_accepted() -> None:
    """自側送信専用への WT_STOP_SENDING 受信が受理されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = server.open_stream(session_id, True)
    assert stream_id == 3

    # ID 3 (自側起点単方向 = 自側送信専用) への STOP_SENDING は受理される
    payload = _encode_varint(3) + _encode_varint(0)
    _inject(server, session_id, _encode_capsule(_WT_STOP_SENDING, payload))

    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    stop_events = [e for e in events if e.type == h2.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert server.get_session_ids() == [session_id]


def test_max_stream_data_to_receive_only_rejected() -> None:
    """自側受信専用への WT_MAX_STREAM_DATA 受信が拒否されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = client.open_stream(session_id, True)
    assert stream_id == 2
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)

    # ID 2 (自側受信専用) への MAX_STREAM_DATA は拒否される
    payload = _encode_varint(2) + _encode_varint(1024)
    _inject(server, session_id, _encode_capsule(_WT_MAX_STREAM_DATA, payload))

    assert len(_stream_state_errors(server)) == 1
    assert server.get_session_ids() == []


def test_max_stream_data_to_send_only_accepted() -> None:
    """自側送信専用への WT_MAX_STREAM_DATA 受信が受理されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = server.open_stream(session_id, True)
    assert stream_id == 3

    # ID 3 (自側送信専用) への MAX_STREAM_DATA は受理される (初期値
    # 262144 を上回る値で増加として扱う)
    payload = _encode_varint(3) + _encode_varint(500000)
    _inject(server, session_id, _encode_capsule(_WT_MAX_STREAM_DATA, payload))

    assert not _stream_state_errors(server)
    assert server.get_session_ids() == [session_id]


def test_reset_to_send_only_rejected() -> None:
    """自側送信専用への WT_RESET_STREAM 受信が拒否されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = server.open_stream(session_id, True)
    assert stream_id == 3

    # ID 3 (自側送信専用) への WT_RESET_STREAM は拒否される
    payload = _encode_varint(3) + _encode_varint(0) + _encode_varint(0)
    _inject(server, session_id, _encode_capsule(_WT_RESET_STREAM, payload))

    assert len(_stream_state_errors(server)) == 1
    assert server.get_session_ids() == []


def test_reset_to_bidi_accepted() -> None:
    """双方向への WT_RESET_STREAM 受信が受理されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ID 0 (双方向) への WT_RESET_STREAM は受理される
    payload = _encode_varint(0) + _encode_varint(0) + _encode_varint(0)
    _inject(server, session_id, _encode_capsule(_WT_RESET_STREAM, payload))

    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    reset_events = [e for e in events if e.type == h2.EventType.STREAM_RESET]
    assert len(reset_events) == 1
    assert server.get_session_ids() == [session_id]


def test_client_mirror_directions() -> None:
    """クライアント視点の送信者→受信者方向の検証を確認

    ID 2 (自側送信専用) への WT_STREAM 受信は拒否される。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ID 2 (自側送信専用) への WT_STREAM は拒否される
    payload = _encode_varint(2) + b"bad"
    assert client.receive(_encode_data_frame(session_id, _encode_capsule(_WT_STREAM, payload))) > 0
    assert len(_stream_state_errors(client)) == 1
    assert client.get_session_ids() == []


def test_client_flow_direction_mirror() -> None:
    """クライアント視点の受信者→送信者方向の検証を確認

    ID 3 (ピア起点単方向 = 自側受信専用) への WT_STOP_SENDING 受信は
    拒否される。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ID 3 への STOP_SENDING は拒否される
    payload = _encode_varint(3) + _encode_varint(0)
    assert (
        client.receive(_encode_data_frame(session_id, _encode_capsule(_WT_STOP_SENDING, payload)))
        > 0
    )
    assert len(_stream_state_errors(client)) == 1
    assert client.get_session_ids() == []
