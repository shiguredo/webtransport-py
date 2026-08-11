"""WebTransport over HTTP/3 の受理前 FIN テスト

サーバーが応答を送信する前に CONNECT ストリームが FIN で閉じられた場合
(受理前 FIN) にセッション終了が検知されない問題の修正を検証する。
受理前 FIN では nghttp3 が WT_SESSION_BLOCKED で空 FIN を処理せず
end_stream コールバックが発火しないため、receive_stream_data の fin 引数
で直接検知し、accept_session による受理と 2xx レスポンスの書き出し完了後に
close_stream で後始末する (draft-ietf-webtrans-http3-16 Section 6 の
セッション終了条件 1 つ目)。
"""

from __future__ import annotations

import pytest
from conftest import _accept_session, _create_session_pair, _pump, _setup_connect

from webtransport import h3


def _drain_events(session: h3.Session) -> list[h3.Event]:
    """セッションに積まれたイベントを全て取り出す"""
    events = []
    while True:
        event = session.next_event()
        if event is None:
            break
        events.append(event)
    return events


@pytest.mark.parametrize(
    "same_read",
    [True, False],
    ids=["same_read", "separate_read"],
)
def test_pre_accept_fin_closes_session(same_read: bool) -> None:
    """受理前 FIN でセッション終了が検知されることを確認

    ヘッダーと FIN の同一読み取りと、別読み取り (空 FIN) の両方で、
    fin 引数による検知が成立し、accept_session 受理後に close_stream で
    後始末されて SessionClosed が error_code 0 で発火する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)

    # 受理前 FIN を渡す (同一読み取りならヘッダー + FIN を同時に、
    # 別読み取りならヘッダーの後に空 FIN を渡す)
    if same_read:
        server.receive_stream_data(0, headers, True)
    else:
        server.receive_stream_data(0, headers, False)
        server.receive_stream_data(0, b"", True)
    assert server.get_session_ids() == [0]

    # アプリが受理する。2xx レスポンスの書き出し前はセッション ID が残る
    # (未送信の 2xx を破棄しないための遅延クローズ)
    assert server.accept_session(0) is True
    assert server.get_session_ids() == [0]

    # 2xx レスポンスの書き出しで遅延クローズが実行される
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)

    # 書き出した 2xx をクライアントに渡し、セッション確立が認識できることを
    # 確認する (遅延クローズの設計根拠: 2xx が破棄されないこと)
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)
    ready_events = [e for e in _drain_events(client) if e.type == h3.EventType.SESSION_READY]
    assert len(ready_events) == 1

    # セッションが終了し、SessionClosed が error_code 0 で発火する
    assert server.get_session_ids() == []
    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert closed_events[0].error_code == 0


def test_pre_accept_fin_normal_session_unaffected() -> None:
    """通常のセッション確立 (FIN なし) が受理前 FIN 検知の影響を受けないことを確認

    FIN なしの通常のセッション確立では受理前 FIN が検知されず、
    SessionClosed も発火しない。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # SESSION_READY が発火し、受理できる
    _accept_session(server)
    server.get_streams_to_send()

    # SessionClosed は発火しない
    assert all(e.type != h3.EventType.SESSION_CLOSED for e in _drain_events(server))


def test_pre_accept_fin_after_accept_no_double_close() -> None:
    """受理後 FIN が二重処理されないことを確認

    サーバーが応答を送信した後に届く FIN (受理後 FIN) は既存の
    end_stream コールバック経路で処理され、受理前 FIN 検知と二重にならない。
    SessionClosed は 1 回だけ発火する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # 受理して 2xx レスポンスを書き出す
    _accept_session(server)
    server.get_streams_to_send()

    # 受理後に空 FIN を渡す (受理後 FIN。end_stream コールバック経路で処理される)
    server.receive_stream_data(0, b"", True)
    server.get_streams_to_send()

    # SessionClosed は 1 回だけ発火する
    assert server.get_session_ids() == []
    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1


def test_pre_accept_fin_not_accepted_keeps_session() -> None:
    """受理されない場合 (reject_session 経路) は SessionClosed が発火しないことを確認

    受理前 FIN を検知しても accept_session が呼ばれなければ遅延クローズは
    実行されず、セッション ID は従来どおり session_ids_ に残る (非 200
    応答時の残留と同じ扱い。現状の挙動を維持する)。検知時点で即クローズ
    する実装のバグ (未送信の 2xx を破棄してしまう) を防ぐ回帰ピンであり、
    修正前実装でも通る。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN

    # 受理せずに 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # SessionClosed は発火せず、セッション ID は残留する
    assert server.get_session_ids() == [0]
    assert all(e.type != h3.EventType.SESSION_CLOSED for e in _drain_events(server))


def test_pre_accept_fin_multiple_sessions() -> None:
    """複数セッション時、受理前 FIN のセッションだけが終了し他が生存することを確認

    確立済みセッション (stream 0) と受理前 FIN のセッション (stream 4) が
    共存するとき、受理前 FIN のセッションの SessionClosed が正しいセッション
    ID で発火し、確立済みセッションは影響を受けない。
    """
    client, server = _create_session_pair()

    # セッション 0 を通常確立する
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)
    _accept_session(server)
    _pump(server, client)
    assert server.get_session_ids() == [0]

    # セッション 4 に受理前 FIN を送る
    assert client.connect(4, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 4)
    server.receive_stream_data(4, headers, True)
    assert server.get_session_ids() == [0, 4]

    # セッション 4 を受理して 2xx を書き出す (遅延クローズ)
    assert server.accept_session(4) is True
    server.get_streams_to_send()

    # セッション 4 だけが終了し、セッション 0 は生存する
    assert server.get_session_ids() == [0]
    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 4
    assert closed_events[0].error_code == 0


def test_pre_accept_fin_blocks_send_and_open_stream() -> None:
    """受理前 FIN 検知後は送信とストリーム開放が拒否されることを確認

    受理前 FIN を検知した時点で終了を学習した状態であり、draft-ietf-webtrans
    -http3-16 Section 6 の MUST (新しいデータグラムを送信せず、新しい
    ストリームも開かない) が、close_stream による後始末までの窓でも満たされる。
    受理前の open_stream は nghttp3 の wt.session 未設定の既存制約でも
    失敗するため、判別力があるのは受理後 (pre_accept_fin_accepted 状態) の
    拒否である (受理後は wt.session が設定され、新規拒否が無ければ成功する)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN

    # 検知後 (受理前) の send_datagram は無視される
    server.send_datagram(0, b"blocked")
    assert server.get_datagrams_to_send() == []

    # 検知後 (受理前) の open_stream は失敗する (nghttp3 の wt.session
    # 未設定の既存制約のピン留め。判別力があるのは受理後の拒否 (下記))
    assert server.open_stream(0, 4, False) is False

    # 受理後も同様に拒否される (close_stream まで)
    assert server.accept_session(0) is True
    server.send_datagram(0, b"blocked")
    assert server.get_datagrams_to_send() == []
    assert server.open_stream(0, 8, False) is False


def test_pre_accept_fin_deferred_close_waits_for_2xx() -> None:
    """2xx レスポンスの書き出し完了まで close_stream が遅延されることを確認

    block_stream で 2xx の書き出しを止めると遅延クローズも保留され、
    session_ids_ が残る (未送信の 2xx を破棄しないため)。ブロック解除後に
    2xx が書き出されてから close_stream が実行され、SessionClosed が発火する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN
    assert server.accept_session(0) is True

    # 2xx の書き出しをブロックする (スケジューラから外れる)
    server.block_stream(0)
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)
    # 2xx 未書き出しのため close_stream は保留され、セッション ID が残る
    assert server.get_session_ids() == [0]

    # ブロック解除後に 2xx が書き出されてから close_stream が実行される
    assert server.unblock_stream(0) is True
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)
    assert server.get_session_ids() == []
    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1


def test_pre_accept_fin_wt_close_session_during_deferred_close() -> None:
    """遅延クローズ保留中に WT_CLOSE_SESSION を受信しても SessionClosed が 1 回だけ発火することを確認

    2xx の書き出し待ちで遅延クローズが保留されている間に WT_CLOSE_SESSION
    を受信すると、セッション終了は recv_wt_close_session_cb 経路で 1 回だけ
    検知される (遅延クローズとの二重発火はしない)。終了済みセッションの
    CONNECT ストリームに未送信の 2xx が後から書き出され得ることは既知の
    制約 (2xx のキャンセル API が無いため)。修正前実装でも通る
    (recv_wt_close_session_cb は遅延クローズ機構に依存しない完全な終了
    経路であり、本テストは新機能との相互作用の回帰ピン)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN
    assert server.accept_session(0) is True

    # 2xx の書き出しをブロックして遅延クローズを保留する
    server.block_stream(0)
    server.get_streams_to_send()
    assert server.get_session_ids() == [0]

    # クライアントが WT_CLOSE_SESSION を送出し、サーバーが受信する
    client.close_session(0, 0)
    _pump(client, server)

    # サーバー側で SessionClosed が 1 回だけ発火する
    closed_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
