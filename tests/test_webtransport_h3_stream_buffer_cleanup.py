"""WebTransport over HTTP/3 のリセット・セッション終了時の送信バッファ解放テスト"""

from __future__ import annotations

import pytest

from webtransport import h3


def _pump(src: h3.Session, dst: h3.Session) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)。get_streams_to_send は
    1 回の呼び出しで全てのデータを返すとは限らない (WT_CLOSE_SESSION 等は
    他のストリームの書き出し後に返る) ため、データが無くなるまで繰り返す
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def _create_session_pair() -> tuple[h3.Session, h3.Session]:
    """h3.Session のクライアント・サーバーペアを作成して初期化する

    @return (クライアント Session, サーバー Session)
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)

    # サーバーの SETTINGS をクライアントに送る
    _pump(server, client)

    return client, server


def _accept_session(server: h3.Session) -> int:
    """サーバー側の SESSION_READY イベントを処理してセッションを受理する

    @return 受理したセッション ID
    """
    session_id = -1
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            assert server.accept_session(event.session_id) is True
            session_id = event.session_id
    assert session_id >= 0, "セッションの受理に失敗しました"
    return session_id


def _drain_session_ready(client: h3.Session) -> int:
    """クライアント側のイベントを全て読み出し、最後の SESSION_READY の
    セッション ID を返す (無ければ -1)。複数の SESSION_READY が積まれて
    いた場合は累積バグとしてテストを失敗させる"""
    session_id = -1
    count = 0
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            session_id = event.session_id
            count += 1
    assert count <= 1, "SESSION_READY が複数回発火しました"
    return session_id


def _connect_session(
    client: h3.Session,
    server: h3.Session,
    stream_id: int,
) -> int:
    """クライアントが CONNECT を送信してセッションを確立する

    @param stream_id CONNECT に使うクライアント起動双方向ストリーム ID
    @return 確立したセッション ID
    """
    assert client.connect(stream_id, "https://localhost/webtransport") is True
    _pump(client, server)
    session_id = _accept_session(server)
    _pump(server, client)
    assert _drain_session_ready(client) == session_id
    return session_id


def _establish_session() -> tuple[h3.Session, h3.Session, int]:
    """h3.Session 同士で WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, セッション ID)
    """
    client, server = _create_session_pair()
    session_id = _connect_session(client, server, 0)
    return client, server, session_id


def _establish_two_sessions() -> tuple[h3.Session, h3.Session, int, int]:
    """h3.Session 同士で 2 つの WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, 1 つ目のセッション ID,
             2 つ目のセッション ID)
    """
    client, server = _create_session_pair()
    first_session_id = _connect_session(client, server, 0)
    second_session_id = _connect_session(client, server, 4)
    return client, server, first_session_id, second_session_id


@pytest.mark.parametrize(
    "reset_method",
    ["close_stream", "reset_stream"],
    ids=["close_stream 経由", "reset_stream 経由"],
)
def test_reset_releases_send_buffer(reset_method: str) -> None:
    """リセットで破棄されたストリームの送信バッファが削除されることを確認

    送信処理 (get_streams_to_send) を挟むと ACK 経路でバッファエントリが
    解放されてしまうため、送信処理を挟まずにリセットする
    """
    client, _server, session_id = _establish_session()

    # データストリームを開いて送信バッファを生成する
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"to-be-reset")

    # リセット前にバッファエントリが存在する
    assert client._has_stream_buffer(stream_id) is True

    # 送信処理を挟まずにリセットする
    if reset_method == "close_stream":
        # close_stream はリセットされたストリームが属するセッション ID を返す
        assert client.close_stream(stream_id, 0) == session_id
    else:
        # reset_stream は close_stream に委譲するだけで戻り値を持たない
        client.reset_stream(stream_id, 0)

    # バッファエントリが削除され、接続は維持される
    assert client._has_stream_buffer(stream_id) is None
    assert client.is_closed() is False


def test_close_session_releases_send_buffers() -> None:
    """close_session と WT_CLOSE_SESSION 受信で送信バッファが削除されることを確認

    close_session 呼び出し側と WT_CLOSE_SESSION 受信側の両方で送信バッファを
    生成しておき、それぞれの削除経路を検証する。2 セッションを張ることで
    対象セッションのバッファのみが削除され、他セッションのバッファが残る
    ことも確認する。検証対象の送信バッファを持つストリームは送信しない
    (送信処理を挟むと ACK 経路でバッファエントリが解放されてしまう)。
    クライアント側の検証は close_session 直後 (送信前) に行い、サーバー側の
    検証は WT_CLOSE_SESSION の転送後に確認する
    """
    client, server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッション: 両側でデータストリームを開いて送信バッファを生成する
    # (クライアント起動双方向ストリームは %4 == 0、サーバー起動は %4 == 1)
    first_client_stream_id = 8
    assert client.open_stream(first_session_id, first_client_stream_id, False) is True
    client.send_stream_data(first_client_stream_id, b"first-client")

    first_server_stream_id = 1
    assert server.open_stream(first_session_id, first_server_stream_id, False) is True
    server.send_stream_data(first_server_stream_id, b"first-server")

    # 2 つ目のセッション: 同様にバッファを生成する
    second_client_stream_id = 12
    assert client.open_stream(second_session_id, second_client_stream_id, False) is True
    client.send_stream_data(second_client_stream_id, b"second-client")

    second_server_stream_id = 5
    assert server.open_stream(second_session_id, second_server_stream_id, False) is True
    server.send_stream_data(second_server_stream_id, b"second-server")

    # 両セッションでバッファエントリが存在する
    assert client._has_stream_buffer(first_client_stream_id) is True
    assert server._has_stream_buffer(first_server_stream_id) is True
    assert client._has_stream_buffer(second_client_stream_id) is True
    assert server._has_stream_buffer(second_server_stream_id) is True

    # close_session 呼び出し側の削除経路を検証する
    client.close_session(first_session_id)
    assert client._has_stream_buffer(first_client_stream_id) is None
    # 他セッションのバッファは残る
    assert client._has_stream_buffer(second_client_stream_id) is True

    # WT_CLOSE_SESSION 受信側の削除経路を検証する
    _pump(client, server)
    assert server._has_stream_buffer(first_server_stream_id) is None
    # 他セッションのバッファは残る
    assert server._has_stream_buffer(second_server_stream_id) is True


def test_connect_stream_reset_releases_session_send_buffers() -> None:
    """CONNECT ストリームのリセットでセッションに属する送信バッファが削除されることを確認

    複数セッションを張った状態で対象セッションの CONNECT ストリームをリセットし、
    対象セッションのバッファのみが削除されて他セッションのバッファが残ることを
    確認する
    """
    client, _server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッションのデータストリーム (次の双方向ストリームは %4 == 0 の 8)
    first_stream_id = 8
    assert client.open_stream(first_session_id, first_stream_id, False) is True
    client.send_stream_data(first_stream_id, b"first")

    # 2 つ目のセッションのデータストリーム (stream_id=12)
    second_stream_id = 12
    assert client.open_stream(second_session_id, second_stream_id, False) is True
    client.send_stream_data(second_stream_id, b"second")

    # 両セッションでバッファエントリが存在する
    assert client._has_stream_buffer(first_stream_id) is True
    assert client._has_stream_buffer(second_stream_id) is True

    # 2 つ目のセッションの CONNECT ストリームをリセットする
    # (セッション ID は CONNECT ストリーム ID そのもの。
    # draft-ietf-webtrans-http3-16 Section 2.2)
    assert client.close_stream(second_session_id, 0) == second_session_id

    # 対象セッションのバッファは削除され、他セッションのバッファは残る
    assert client._has_stream_buffer(second_stream_id) is None
    assert client._has_stream_buffer(first_stream_id) is True

    # close_stream の同期コールバックで発火する ResetStream / StopSending
    # イベントのセッション ID が、stream_info_ の残存により正しく復元される
    # ことを確認する (stream_info_ エントリを残す設計の根拠)
    reset_events = []
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type in (h3.EventType.RESET_STREAM, h3.EventType.STOP_SENDING):
            reset_events.append((event.type, event.session_id, event.stream_id))
    assert reset_events
    for _event_type, event_session_id, event_stream_id in reset_events:
        assert event_session_id == second_session_id
        assert event_stream_id == second_stream_id
