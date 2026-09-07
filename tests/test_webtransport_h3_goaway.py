"""WebTransport over HTTP/3 の GOAWAY 受信テスト

GOAWAY 受信は graceful shutdown の通知であり、接続エラーではない
(draft-ietf-webtrans-http3-16 Section 4.7)。受信後も is_closed() は偽の
ままであり、確立済みセッションの送受信を継続できる。GOAWAY フレームは
サーバーの制御ストリーム (サーバー起点単方向) に注入する。
"""

from __future__ import annotations

from conftest import _create_session_pair, _drain_events, _establish_session, _pump

from webtransport import h3

# GOAWAY フレーム (種別 0x07 + 長さ + ID)。ID は自起点双方向ストリームの
# 空間でなければならず、増加は不正のため、複数回は減少方向で送る
# (graceful shutdown の絞り込みが正規の使い方)
GOAWAY_ID_8 = b"\x07\x01\x08"
GOAWAY_ID_4 = b"\x07\x01\x04"
GOAWAY_ID_0 = b"\x07\x01\x00"

# サーバー起点単方向ストリーム (サーバーの制御ストリーム相当)
SERVER_UNI_STREAM_ID = 3


def test_goaway_keeps_session_usable() -> None:
    """GOAWAY 受信後もセッションが継続できる"""
    client, _server, session_id = _establish_session()

    # GOAWAY 受信前は閉じていない
    assert client.is_closed() is False

    # GOAWAY を受信する
    client.receive_stream_data(SERVER_UNI_STREAM_ID, GOAWAY_ID_0, False)

    # 接続エラーではないため閉じない
    assert client.is_closed() is False

    # GoAway イベント (GOAWAY ID 付き) が 1 件発火する
    goaway_events = [event for event in _drain_events(client) if event.type == h3.EventType.GOAWAY]
    assert len(goaway_events) == 1
    assert goaway_events[0].goaway_id == 0

    # 確立済みセッションのデータグラム送信は継続できる
    client.send_datagram(session_id, b"goaway-datagram")
    assert len(client.get_datagrams_to_send()) == 1


def test_goaway_existing_stream_sendable() -> None:
    """GOAWAY 受信後も確立済みストリームの送信が継続できる"""
    client, _server, session_id = _establish_session()

    # GOAWAY 受信前に双方向ストリームを開く
    assert client.open_stream(session_id, 4, False) is True

    # GOAWAY を受信する
    client.receive_stream_data(SERVER_UNI_STREAM_ID, GOAWAY_ID_0, False)
    assert client.is_closed() is False

    # 確立済みストリームへの送信は継続できる (書き出しキューに載る)
    client.send_stream_data(4, b"after-goaway", False)
    queued = [stream_id for stream_id, _data, _fin in client.get_streams_to_send()]
    assert 4 in queued


def test_goaway_duplicate_events() -> None:
    """GOAWAY の複数回受信は低レベルでは各回イベントになる"""
    client, _server, _session_id = _establish_session()

    # GOAWAY を 2 回受信する (ID 減少方向で区別する。増加は不正のため
    # 接続エラーになる)
    client.receive_stream_data(SERVER_UNI_STREAM_ID, GOAWAY_ID_8, False)
    client.receive_stream_data(SERVER_UNI_STREAM_ID, GOAWAY_ID_4, False)
    assert client.is_closed() is False

    # 低レベルでは各回イベントになる (多重発火の抑止は高レベル層の責務)
    goaway_events = [event for event in _drain_events(client) if event.type == h3.EventType.GOAWAY]
    assert [event.goaway_id for event in goaway_events] == [8, 4]


def test_new_open_refused_after_goaway() -> None:
    """GOAWAY 受信後の新規 open は依存側が拒否する"""
    client, _server, session_id = _establish_session()

    # GOAWAY を受信する
    client.receive_stream_data(SERVER_UNI_STREAM_ID, GOAWAY_ID_0, False)
    assert client.is_closed() is False

    # 同梱 nghttp3 は GOAWAY 受信後の新規ストリーム開放を拒否するため、
    # 新規 open は失敗する (draft-16 Section 4.7 の MAY は許容規定であり、
    # 拒否しても仕様違反ではない)。is_closed() が偽のままであることが重要
    # であり、セッション自体は継続する
    assert client.open_stream(session_id, 8, False) is False
    assert client.is_closed() is False


def test_server_goaway_event() -> None:
    """サーバー側セッションの GOAWAY 受信で GoAway イベントが発火する"""
    client, server = _create_session_pair()

    # クライアントの制御系ストリームをサーバーへ流して制御ストリームを
    # 確立する (実フローと同様に SETTINGS から処理させる)
    for stream_id, data, fin in client.get_streams_to_send():
        server.receive_stream_data(stream_id, data, fin)
    assert server.is_closed() is False

    # クライアントの制御ストリーム相当 (クライアント起点単方向) に
    # GOAWAY フレームを注入する
    server.receive_stream_data(2, GOAWAY_ID_8, False)
    assert server.is_closed() is False

    # GoAway イベント (GOAWAY ID 付き) が 1 件発火する
    goaway_events = [event for event in _drain_events(server) if event.type == h3.EventType.GOAWAY]
    assert len(goaway_events) == 1
    assert goaway_events[0].goaway_id == 8

    # pump で送受信が継続できる
    _pump(client, server)
    _pump(server, client)
    assert server.is_closed() is False


def test_connection_error_still_closes() -> None:
    """接続エラーでは従来どおり閉じることを確認

    パリティ違反のストリームを受信すると接続エラーで閉じる。
    """
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # パリティ違反のストリームを受信すると接続エラーで閉じる
    ret = server.receive_stream_data(1, b"\x00", False)
    assert ret == 0
    assert server.is_closed() is True
