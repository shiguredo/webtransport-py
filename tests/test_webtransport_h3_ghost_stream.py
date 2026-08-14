"""WebTransport over HTTP/3 の ghost ストリーム配信テスト

close_session (WT_CLOSE_SESSION 送出) と recv_wt_close_session_cb
(WT_CLOSE_SESSION 受信) は session_ids_ からセッション ID を削除するが、
nghttp3 の CONNECT ストリームはストリームテーブルに残存するため、セッション
終了後にピアが開いたデータストリーム (ghost ストリーム) は nghttp3 が受容
して recv_wt_data_cb が呼ばれる。この ghost ストリームのデータがアプリに
配信されないこと (draft-ietf-webtrans-http3-16 Section 6 の MUST「終了を
学習したエンドポイントは、属するストリームの受信側の読み取りを中止しなけれ
ばならない」) を検証する。
"""

from __future__ import annotations

from conftest import (
    _create_session_pair,
    _drain_events,
    _encode_wt_stream_data,
    _establish_session,
    _establish_two_sessions,
    _pump,
    _setup_connect,
)

from webtransport import h3


def test_ghost_stream_after_recv_wt_close_session_ignored() -> None:
    """WT_CLOSE_SESSION 受信後の ghost ストリームが配信されないことを確認

    サーバーが WT_CLOSE_SESSION を受信してセッション終了を学習した後、
    そのセッション ID 宛のデータストリームのデータはアプリに配信されない。
    nghttp3 の CONNECT ストリームが残存するため recv_wt_data_cb は呼ばれる
    が、session_ids_ のメンバーシップ確認で破棄される。
    """
    client, server, session_id = _establish_session()

    # クライアントが WT_CLOSE_SESSION を送出し、サーバーが受信する
    client.close_session(session_id, 0)
    _pump(client, server)
    assert server.get_session_ids() == []

    # ghost ストリームを注入する (双方向ストリーム 4、セッション ID 0)
    server.receive_stream_data(4, _encode_wt_stream_data(session_id, b"ghost"), False)

    # STREAM_DATA は配信されない
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(server))


def test_ghost_stream_after_sending_close_session_ignored() -> None:
    """close_session 送出後の ghost ストリームが配信されないことを確認

    close_session で WT_CLOSE_SESSION を送出した側 (クライアント) は
    session_ids_ から削除済みだが、nghttp3 の CONNECT ストリームは送出側
    でもストリームテーブルに残存する。そのためピア (サーバー) が開いた
    データストリームは nghttp3 が受容して recv_wt_data_cb が呼ばれるが、
    session_ids_ のメンバーシップ確認で破棄される。クライアントが受信する
    データストリームはサーバー起動の双方向ストリーム (ストリーム ID %4==1)
    である。
    """
    client, _server, session_id = _establish_session()

    # クライアントが WT_CLOSE_SESSION を送出する
    client.close_session(session_id, 0)
    assert client.get_session_ids() == []

    # ghost ストリームを注入する (サーバー起動双方向ストリーム 5)
    client.receive_stream_data(5, _encode_wt_stream_data(session_id, b"ghost"), False)

    # STREAM_DATA は配信されない
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(client))


def test_ghost_stream_alive_session_delivered() -> None:
    """生存セッションのデータストリームは従来どおり配信されることを確認

    session_ids_ に存在するセッション ID 宛のデータストリームは、ghost
    ストリームの破棄の影響を受けずに配信される。
    """
    _client, server, session_id = _establish_session()

    server.receive_stream_data(4, _encode_wt_stream_data(session_id, b"hello"), False)

    stream_data_events = [e for e in _drain_events(server) if e.type == h3.EventType.STREAM_DATA]
    assert len(stream_data_events) == 1
    assert stream_data_events[0].session_id == session_id
    assert stream_data_events[0].stream_id == 4
    assert stream_data_events[0].data == b"hello"


def test_ghost_stream_fin_closes_with_minus_one() -> None:
    """破棄した ghost ストリームの後続 FIN で session_id = -1 が発火することを確認

    ghost ストリームは stream_info_ に未登録のため、ピアの FIN と送信側の
    クローズが揃うと stream_close_cb が session_id = -1 で発火する
    (既存の未登録ストリームと同じ挙動)。
    """
    client, server, session_id = _establish_session()
    client.close_session(session_id, 0)
    _pump(client, server)

    # ghost ストリームを注入して破棄する
    server.receive_stream_data(4, _encode_wt_stream_data(session_id, b"ghost"), False)

    # ピアの FIN と送信側のクローズ (QUIC 層の RESET 相当) を送る
    server.receive_stream_data(4, b"", True)
    server.close_stream(4, 0)

    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.STREAM_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].stream_id == 4
    assert closed_events[0].session_id == -1


def test_ghost_stream_reset_closes_with_minus_one() -> None:
    """破棄した ghost ストリームの後続 RESET で session_id = -1 が発火することを確認

    ピアからの RESET (QUIC 層の STREAM_RESET を受けた高レベル層が
    close_stream を呼ぶ) で stream_close_cb が session_id = -1 で発火する。
    """
    client, server, session_id = _establish_session()
    client.close_session(session_id, 0)
    _pump(client, server)

    # ghost ストリームを注入して破棄する
    server.receive_stream_data(4, _encode_wt_stream_data(session_id, b"ghost"), False)

    # ピアからの RESET 相当のクローズを通知する
    server.close_stream(4, 0x100)

    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.STREAM_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].stream_id == 4
    assert closed_events[0].session_id == -1


def test_ghost_stream_after_pre_accept_fin_ignored() -> None:
    """受理前 FIN 検知済みセッションへのデータストリームが配信されないことを確認

    受理前 FIN を検知したセッション (終了を学習済みだが close_stream による
    後始末前) は session_ids_ に含まれたままのため、宛先のデータストリーム
    が nghttp3 に受容されて recv_wt_data_cb が呼ばれる。send_datagram /
    open_stream の送信拒否と同じく、終了を学習したセッションへの配信は
    抑止される (draft-ietf-webtrans-http3-16 Section 6 の MUST)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN を検知する
    assert server.accept_session(0) is True

    # 受理前 FIN 検知済みセッション宛のデータストリームを注入する
    server.receive_stream_data(4, _encode_wt_stream_data(0, b"ghost"), False)

    # STREAM_DATA は配信されない
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(server))


def test_ghost_stream_after_client_recv_wt_close_session_ignored() -> None:
    """クライアントが WT_CLOSE_SESSION を受信した後の ghost が配信されないことを確認

    サーバーが close_session で WT_CLOSE_SESSION を送出し、クライアントが
    受信してセッション終了を学習した後、そのセッション ID 宛のデータ
    ストリームは配信されない。クライアントが受信するデータストリームは
    サーバー起動の双方向ストリーム (ストリーム ID %4==1) である。
    """
    client, server, session_id = _establish_session()

    # サーバーが WT_CLOSE_SESSION を送出し、クライアントが受信する
    server.close_session(session_id, 0)
    _pump(server, client)
    assert client.get_session_ids() == []

    # ghost ストリームを注入する (サーバー起動双方向ストリーム 5)
    client.receive_stream_data(5, _encode_wt_stream_data(session_id, b"ghost"), False)

    # STREAM_DATA は配信されない
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(client))


def test_ghost_stream_multiple_sessions() -> None:
    """複数セッション共存時、終了セッション宛 ghost だけが破棄されることを確認

    終了したセッション宛のデータストリームの破棄はセッション ID 単位で
    行われ、生存セッション宛の配信には波及しない。注入するストリーム ID
    は既存の CONNECT ストリーム (0, 4) と重複しないものを使う。
    """
    client, server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッションを終了する (クライアントが WT_CLOSE_SESSION を送出)
    client.close_session(first_session_id, 0)
    _pump(client, server)
    assert server.get_session_ids() == [second_session_id]

    # 終了セッション宛の ghost を注入する (ストリーム 12)
    server.receive_stream_data(12, _encode_wt_stream_data(first_session_id, b"ghost"), False)
    # 生存セッション宛のデータを注入する (ストリーム 8)
    server.receive_stream_data(8, _encode_wt_stream_data(second_session_id, b"alive"), False)

    stream_data_events = [e for e in _drain_events(server) if e.type == h3.EventType.STREAM_DATA]
    # 生存セッション宛のみ配信される
    assert len(stream_data_events) == 1
    assert stream_data_events[0].session_id == second_session_id
    assert stream_data_events[0].data == b"alive"


def test_ghost_stream_client_alive_session_delivered() -> None:
    """クライアント側の生存セッションでデータストリームが配信されることを確認

    クライアントが受信するデータストリームはサーバー起動の双方向ストリーム
    (ストリーム ID %4==1) である。生存セッションでは破棄されずに配信される
    (クライアント側の非破棄分岐の確認)。
    """
    client, _server, session_id = _establish_session()

    client.receive_stream_data(5, _encode_wt_stream_data(session_id, b"hello"), False)

    stream_data_events = [e for e in _drain_events(client) if e.type == h3.EventType.STREAM_DATA]
    assert len(stream_data_events) == 1
    assert stream_data_events[0].session_id == session_id
    assert stream_data_events[0].data == b"hello"


def test_late_stream_after_connect_stream_close_gone() -> None:
    """CONNECT ストリームのクローズ経路の late データストリーム破棄が回帰しないことを確認

    close_stream による CONNECT ストリームのクローズでは nghttp3 が
    CONNECT ストリームを削除するため、late データストリームは
    WT_SESSION_GONE で破棄される (既に正しく動作する経路の回帰確認)。
    """
    _client, server, session_id = _establish_session()

    # CONNECT ストリームをクローズしてセッションを終了する
    server.close_stream(session_id, 0)
    assert server.get_session_ids() == []

    # late データストリームを注入する
    server.receive_stream_data(4, _encode_wt_stream_data(session_id, b"late"), False)

    # STREAM_DATA は配信されない (WT_SESSION_GONE 破棄)
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(server))
