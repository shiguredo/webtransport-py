"""WebTransport over HTTP/3 のリセット・セッション終了時の送信バッファ解放テスト"""

from __future__ import annotations

import pytest
from conftest import _drain_events, _establish_session, _establish_two_sessions, _pump

from webtransport import h3


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
    # イベントのセッション ID が、nghttp3_conn_close_stream 呼び出し時点の
    # stream_info_ の残存により正しく復元されることを確認する (セッション
    # 終了の後始末 (erase_session_streams) は同期コールバックの後に実行
    # されるため、コールバック時点ではエントリが残っている)
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


def test_second_connect_stream_reset_returns_minus_one() -> None:
    """同一 CONNECT ストリームの 2 回目のリセットで -1 が返ることを確認

    1 回目のリセットでセッションが終了して session_ids_ から削除されるため、
    2 回目の同一 CONNECT ストリームのリセットではセッション ID を復元できず
    -1 が返る (データストリームの二重リセットの -1 と対称)
    """
    client, _server, _first_session_id, second_session_id = _establish_two_sessions()

    # 1 回目のリセットではセッション ID (= CONNECT ストリーム ID) が返る
    assert client.close_stream(second_session_id, 0) == second_session_id

    # 2 回目のリセットではセッション終了済みのため復元できず -1 が返る
    assert client.close_stream(second_session_id, 0) == -1


def test_connect_stream_reset_cleans_session_streams() -> None:
    """CONNECT ストリームのリセットでセッションに属するストリーム情報が清掃されることを確認

    複数セッションを確立し、あるセッションで開いたデータストリームが、
    CONNECT ストリームのリセットによるセッション終了で stream_info_ から
    削除されることを確認する。清掃されないと get_session_streams が
    終了したセッションの stale エントリを返し続ける
    """
    client, _server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッションでデータストリームを開く
    first_stream_id = 8
    assert client.open_stream(first_session_id, first_stream_id, False) is True
    assert client.get_session_streams(first_session_id) != []
    assert client.get_session_streams(second_session_id) == []

    # 1 つ目のセッションの CONNECT ストリームをリセットする
    assert client.close_stream(first_session_id, 0) == first_session_id

    # 終了したセッションのストリーム情報が清掃される
    assert client.get_session_streams(first_session_id) == []

    # 他セッションのストリーム情報は清掃されない
    second_stream_id = 12
    assert client.open_stream(second_session_id, second_stream_id, False) is True
    assert client.get_session_streams(second_session_id) != []


def test_connect_stream_fin_cleans_session_streams() -> None:
    """CONNECT ストリームの FIN でセッションに属するストリーム情報が清掃されることを確認

    複数セッションを確立し、あるセッションで開いたデータストリームが、
    CONNECT ストリームの FIN (クリーンクローズ) によるセッション終了で
    stream_info_ から削除されることを確認する。清掃されないと
    get_session_streams が終了したセッションの stale エントリを返し続ける
    """
    client, _server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッションでデータストリームを開く
    first_stream_id = 8
    assert client.open_stream(first_session_id, first_stream_id, False) is True
    assert client.get_session_streams(first_session_id) != []
    assert client.get_session_streams(second_session_id) == []

    # 1 つ目のセッションの CONNECT ストリームに空 FIN を届ける
    # (Sans-IO 構成のため receive_stream_data で直接渡す)
    client.receive_stream_data(first_session_id, b"", fin=True)

    # 終了したセッションのストリーム情報が清掃される
    assert client.get_session_streams(first_session_id) == []

    # 終了したセッションが session_ids_ から削除される
    assert client.get_session_ids() == [second_session_id]

    # 他セッションのストリーム情報は清掃されない
    second_stream_id = 12
    assert client.open_stream(second_session_id, second_stream_id, False) is True
    assert client.get_session_streams(second_session_id) != []

    # FIN 経路の SessionClosed イベントは 1 回だけ発火し、error_code は 0
    # (クリーンクローズ。WT_CLOSE_SESSION 無しの FIN は error code 0 かつ
    # 空のエラー文字列の WT_CLOSE_SESSION と等価。draft-ietf-webtrans-http3-16
    # Section 6)
    session_closed = None
    session_closed_count = 0
    reset_events = []
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_CLOSED:
            session_closed = event
            session_closed_count += 1
        if event.type in (h3.EventType.RESET_STREAM, h3.EventType.STOP_SENDING):
            reset_events.append((event.type, event.session_id, event.stream_id))
    assert session_closed_count == 1
    assert session_closed is not None
    assert session_closed.session_id == first_session_id
    assert session_closed.error_code == 0

    # close_stream の同期コールバックで、セッションに属するデータストリームが
    # WT_SESSION_GONE で破棄され、ResetStream / StopSending イベントが正しい
    # セッション ID で発火することを確認する (draft-ietf-webtrans-http3-16
    # Section 6 の MUST。nghttp3_conn_close_stream が nghttp3 内部で破棄する)
    assert reset_events
    for _event_type, event_session_id, event_stream_id in reset_events:
        assert event_session_id == first_session_id
        assert event_stream_id == first_stream_id


def test_fin_then_second_close_returns_minus_one() -> None:
    """FIN でセッション終了後に close_stream を再度呼ぶと -1 が返ることを確認

    1 回目の FIN でセッションが終了して session_ids_ から削除されるため、
    2 回目の同一 CONNECT ストリームの close_stream ではセッション ID を
    復元できず -1 が返り、SessionClosed も追加発火しない (データストリームの
    二重クローズの -1 と対称)
    """
    client, _server, session_id = _establish_session()

    # FIN でセッションを終了する
    client.receive_stream_data(session_id, b"", fin=True)

    # SessionClosed が 1 回だけ発火する
    session_closed_count = 0
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_CLOSED:
            session_closed_count += 1
    assert session_closed_count == 1

    # 2 回目の close_stream ではセッション終了済みのため -1 が返る
    assert client.close_stream(session_id, 0) == -1

    # 2 回目の close_stream で SessionClosed は追加発火しない
    session_closed_count = 0
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_CLOSED:
            session_closed_count += 1
    assert session_closed_count == 0


def test_send_to_unregistered_stream_is_ignored() -> None:
    """未登録ストリームへの送信がどのセッションにも配送されないことを確認

    複数セッションを確立した構成で、どのセッションにも属さない未登録の
    クライアント起動双方向ストリーム (%4 == 0) に送信しても、受信側の
    イベントにデータが現れず、送信側の送信バッファにもエントリが残らない
    ことを確認する。旧実装ではセッション ID 集合の先頭要素がセッション ID
    として使われ、生存セッションに誤配送されていた
    """
    client, server, _first_session_id, _second_session_id = _establish_two_sessions()

    # どのセッションにも属さない未登録の双方向ストリーム ID を使う
    # (確立済み CONNECT は 0 と 4、次の双方向ストリームは %4 == 0 の 8)
    unregistered_stream_id = 8

    # 未登録ストリームへの送信は黙って無視される
    client.send_stream_data(unregistered_stream_id, b"stray-data")
    _pump(client, server)

    # 送信側の送信バッファにエントリが残らない
    assert client._has_stream_buffer(unregistered_stream_id) is None

    # 受信側のイベントにデータが現れない (どのセッションにも配送されない)
    received = _drain_events(server)
    assert not any(
        event.type == h3.EventType.STREAM_DATA and event.data == b"stray-data" for event in received
    ), "未登録ストリームへのデータが受信側に配送されました"


def test_send_to_closed_session_stream_is_ignored() -> None:
    """終了したセッションのストリームへの事後送信が誤ったセッションに配送されないことを確認

    複数セッションを確立し、あるセッションで open_stream したストリームに
    対して close_session でセッションを終了した後に send_stream_data しても、
    どのセッションにも配送されず、送信側の送信バッファにもエントリが残らない
    ことを確認する。close_session は erase_session_streams で stream_info_
    を清掃するため、事後送信されたストリームは未登録扱いになる。旧実装では
    セッション ID 集合の先頭要素 (生存セッション) への誤配送に加え、
    nghttp3_conn_open_wt_data_stream がプロセスを abort させていた
    """
    client, server, first_session_id, _second_session_id = _establish_two_sessions()

    # 1 つ目のセッションでデータストリームを開く
    dead_stream_id = 8
    assert client.open_stream(first_session_id, dead_stream_id, False) is True

    # セッションを終了する (WT_CLOSE_SESSION を送出して送信側の後始末を行う)
    client.close_session(first_session_id)
    _pump(client, server)

    # 終了したセッションのストリームへの事後送信は黙って無視される
    client.send_stream_data(dead_stream_id, b"after-death")
    _pump(client, server)

    # 送信側の送信バッファにエントリが残らない
    assert client._has_stream_buffer(dead_stream_id) is None

    # 受信側のイベントにデータが現れない (生存セッションにも誤配送されない)
    received = _drain_events(server)
    assert not any(
        event.type == h3.EventType.STREAM_DATA and event.data == b"after-death"
        for event in received
    ), "終了したセッションのストリームへのデータが受信側に配送されました"


@pytest.mark.parametrize(
    "direction",
    ["client_uni", "server_uni"],
    ids=["クライアント起点 %4==2 受信", "サーバー起点 %4==3 受信"],
)
def test_send_to_received_uni_stream_is_ignored(direction: str) -> None:
    """受信済み単方向ストリームへの送信が黙って無視されることを確認

    ピアが開いた単方向ストリーム (クライアント起点 %4==2 / サーバー起点
    %4==3) に受信側が送信すると、nghttp3 の書き込み登録
    (nghttp3_conn_open_wt_data_stream) がストリームの方向を assert で検査し、
    デバッグビルドでプロセスが abort し得る (単方向ストリームは送信方向が
    一方向のみ (RFC 9000 Section 2.1) のため、受信側からの書き込みは不正な
    利用である)。受信済み単方向ストリームへの送信は黙って無視され、
    送信バッファにエントリが残らず、書き込み登録も行われないことを確認する。
    判別は送信側の _has_stream_buffer が None になること (旧実装では
    True のまま) を送信処理 (_pump) を挟む前のタイミングで確認することで
    行う。受信側のイベント確認は判別力を持たない (受信側は nghttp3 の
    SHUT_RD フラグにより受信データを黙って消費するため)
    """
    client, server, session_id = _establish_session()

    if direction == "client_uni":
        # クライアントが単方向ストリーム (%4 == 2) を開いてデータを送る
        uni_stream_id = 14
        assert client.open_stream(session_id, uni_stream_id, True) is True
        client.send_stream_data(uni_stream_id, b"request")
        _pump(client, server)
        receiver = server
    else:
        # サーバーが単方向ストリーム (%4 == 3) を開いてデータを送る
        uni_stream_id = 15
        assert server.open_stream(session_id, uni_stream_id, True) is True
        server.send_stream_data(uni_stream_id, b"request")
        _pump(server, client)
        receiver = client

    # 受信側に受信済み単方向ストリームとして登録されている
    entry = next(
        s for s in receiver.get_session_streams(session_id) if s.stream_id == uni_stream_id
    )
    assert entry.is_unidirectional is True
    assert entry.is_incoming is True
    assert entry.is_write_registered is False

    # 受信済み単方向ストリームへの送信は黙って無視される
    receiver.send_stream_data(uni_stream_id, b"reply")
    assert receiver._has_stream_buffer(uni_stream_id) is None
    # 書き込み登録も行われない (is_write_registered が False のまま)
    entry = next(
        s for s in receiver.get_session_streams(session_id) if s.stream_id == uni_stream_id
    )
    assert entry.is_write_registered is False
