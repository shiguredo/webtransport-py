"""WebTransport over HTTP/2 の GOAWAY 受信テスト

GOAWAY 受信後も既存セッションが継続し、新規 CONNECT のみ抑止されること
(draft-15 Section 6.13 の graceful shutdown) を検証する。GOAWAY フレーム
は送出手段が存在しないためワイヤ注入で再現する。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_goaway_frame,
    _h2_pump,
)

from webtransport import h2


def _goaway_events(session: h2.Session) -> list:
    """GoAway イベントを取り出す"""
    return [event for event in _drain_events(session) if event.type == h2.EventType.GOAWAY]


def test_goaway_keeps_session_usable() -> None:
    """GOAWAY 受信後も既存セッションで送受信できることを確認

    GoAway イベント (last_stream_id と error_code 付き) が発火し、
    is_closed() は偽のまま、データグラムの往復が継続する
    """
    client, server = _create_h2_session_pair()
    # _connect_h2_session は確立と受理まで行う
    session_id = _connect_h2_session(client, server)

    # サーバーからクライアントへ GOAWAY を注入する
    client.receive(_encode_goaway_frame(session_id, 0))

    # GoAway が発火し、セッションは閉じない
    goaway_events = _goaway_events(client)
    assert len(goaway_events) == 1
    assert goaway_events[0].last_stream_id == session_id
    assert goaway_events[0].error_code == 0
    assert client.is_closed() is False
    assert server.is_closed() is False

    # 既存セッションでデータグラムの往復が継続する
    client.send_datagram(session_id, b"after-goaway")
    _h2_pump(client, server)
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"after-goaway"


def test_goaway_suppresses_new_connect() -> None:
    """GOAWAY 受信後は新規 CONNECT が抑止されることを確認

    既存セッション上の WT ストリーム open は継続する
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    client.receive(_encode_goaway_frame(session_id, 0))
    assert len(_goaway_events(client)) == 1

    # 新規 CONNECT は拒否される
    assert client.connect("https://localhost/other") == -1

    # 既存セッション上のストリーム open は継続する
    assert client.open_stream(session_id, False) >= 0
    assert server.is_closed() is False


def test_goaway_server_side_keeps_session() -> None:
    """サーバー側の GOAWAY 受信でもセッションが継続することを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントからサーバーへ GOAWAY を注入する。last_stream_id は
    # サーバー起点ストリームを指すため、存在しない場合は 0 とする
    # (parity 不整合の値は nghttp2 が黙って無視するため)
    server.receive(_encode_goaway_frame(0, 0))

    goaway_events = _goaway_events(server)
    assert len(goaway_events) == 1
    assert goaway_events[0].last_stream_id == 0
    assert server.is_closed() is False

    # サーバーからクライアントへの送信が継続する
    server.send_datagram(session_id, b"server-after-goaway")
    _h2_pump(server, client)
    datagram_events = [
        event for event in _drain_events(client) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"server-after-goaway"


def test_goaway_reports_frame_values() -> None:
    """GOAWAY フレームの値がイベントに載ることを確認

    last_stream_id と error_code がそのまま伝わる
    """
    client, _server = _create_h2_session_pair()

    client.receive(_encode_goaway_frame(5, 0x01))

    goaway_events = _goaway_events(client)
    assert len(goaway_events) == 1
    assert goaway_events[0].last_stream_id == 5
    assert goaway_events[0].error_code == 0x01
    assert client.is_closed() is False
