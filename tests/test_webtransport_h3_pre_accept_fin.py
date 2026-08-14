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


def test_pre_accept_fin_not_accepted_removes_session_id() -> None:
    """受理前 FIN を検知済みのセッションを非 2xx で拒否しても SessionClosed が発火しないことを確認

    受理前 FIN 検知済みセッションの拒否は、受理前 FIN なしの拒否と状態の
    組み合わせが異なる回帰ピン。非 2xx 拒否で session_ids_ から削除され、
    SessionClosed は発火しない (黙って削除: 一度も確立されていないセッション
    の終了通知という意味論が合わない)。受理前 FIN 検知時に即クローズしない
    (未送信の 2xx を破棄しない) という本質の検証は
    test_pre_accept_fin_deferred_close_waits_for_2xx が担う。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN

    # 受理せずに 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # セッション ID は削除され、SessionClosed は発火しない
    assert server.get_session_ids() == []
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
    CONNECT ストリームに残った未送信の 2xx は、close_stream による破棄で
    書き出されない (ブロック解除後の get_streams_to_send に現れない)。
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
    events = _drain_events(server)
    closed_events = [e for e in events if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1

    # 破棄の close_stream による STREAM_CLOSED も 1 回だけ発火する。
    # pre_accept_fin_accepted_session_ids_ からの除去漏れがあっても、現在の
    # 依存 nghttp3 では 2 回目の close_stream がストリーム未存在で
    # stream_close_cb を発火しないため検出できないが、nghttp3 の実装変更で
    # イベント個数が変わり得るため、除去の防衛をピン留めする
    stream_closed_events = [e for e in events if e.type == h3.EventType.STREAM_CLOSED]
    assert len(stream_closed_events) == 1

    # ブロック解除しても未送信の 2xx は書き出されない
    assert server.unblock_stream(0) is True
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)


def test_pre_accept_fin_local_close_session_2xx_sent() -> None:
    """遅延クローズ保留中のローカル close_session では 2xx が送出される既知の制約を確認

    ローカル close_session (WT_CLOSE_SESSION 送出) では、カプセルと未送信の
    2xx が同一の nghttp3 送信キューにあるため、2xx のみを破棄する手段が
    ない。close_stream で破棄するとカプセル (error code / message) も失われ、
    ピアに終了情報が伝わらないため、送出経路は 2xx の送出を許容する
    (既知の制約)。ピアは 2xx を受信して SESSION_READY が発火した後、
    WT_CLOSE_SESSION による SessionClosed が発火することを固定する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN
    assert server.accept_session(0) is True

    # 2xx の書き出しをブロックして遅延クローズを保留する
    server.block_stream(0)
    server.get_streams_to_send()

    # サーバーがローカル close_session を呼ぶ (WT_CLOSE_SESSION 送出)
    server.close_session(0, 0)

    # ブロック解除すると、2xx + WT_CLOSE_SESSION カプセルが一体で送出される
    assert server.unblock_stream(0) is True
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)

    # ピア (クライアント) は 2xx をセッション確立として処理して SESSION_READY
    # が発火し、続く WT_CLOSE_SESSION で SessionClosed が発火する
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)
    events = _drain_events(client)
    ready_events = [e for e in events if e.type == h3.EventType.SESSION_READY]
    assert len(ready_events) == 1
    assert ready_events[0].session_id == 0
    closed_events = [e for e in events if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0


def _send_pre_accept_wt_close_session(client: h3.Session) -> bytes:
    """クライアントが受理前に WT_CLOSE_SESSION を送出する

    クライアントの close_session で WT_CLOSE_SESSION カプセルを送信キューに
    積み、get_streams_to_send で取り出して返す (サーバーへの注入は呼び出し
    側が行う)。受理前のカプセルはサーバー側で nghttp3 の inq にバッファされ、
    accept_session の confirm 処理中に同期処理される (draft-ietf-webtrans
    -http3-16 Section 3.2 の「A server MUST NOT process these bytes as
    capsules until it sends a 2xx response accepting the session」)。

    get_streams_to_send が WT_CLOSE_SESSION カプセル 1 件だけを返すことは、
    _setup_connect が CONNECT ヘッダーを書き出し済みであることに依存する
    (クライアントの送信キューに残っているのはカプセルのみ)。

    @param client クライアントセッション (connect 済み)
    @return WT_CLOSE_SESSION カプセルのデータ
    """
    client.close_session(0, 0)
    streams = client.get_streams_to_send()
    assert len(streams) == 1, "WT_CLOSE_SESSION カプセル以外の送信データがあります"
    wt_close_stream_id, wt_close_data, wt_close_fin = streams[0]
    assert wt_close_stream_id == 0, "CONNECT ストリーム以外の送信データがあります"
    # nghttp3 は WT_CLOSE_SESSION 送出時に FIN も付ける
    # (draft-ietf-webtrans-http3-16 Section 6 の MUST「準拠クライアントは
    # WT_CLOSE_SESSION 直後に FIN を送る」を満たす)。サーバーへの注入時に
    # fin を False にすれば「FIN が別パケットで届く」変種を構成できる
    assert wt_close_fin is True, "WT_CLOSE_SESSION カプセルには FIN が付きます"
    return wt_close_data


def _assert_wt_close_session_discarded(server: h3.Session) -> None:
    """受理前 WT_CLOSE_SESSION によるセッション終了の後始末をまとめて検証する

    SessionClosed と STREAM_CLOSED がそれぞれ 1 回だけ発火し (accept_session
    内の破棄と receive_stream_data 後段の二重 close_stream の回帰検出)、
    セッション ID が残留せず、未送信の 2xx も送出されないことを確認する。
    STREAM_CLOSED の個数は nghttp3 の実装順序に依存するピンである (将来の
    nghttp3 で WT_CLOSE_SESSION 処理時に CONNECT ストリームが内部削除され
    ると close_stream がストリーム未存在になり 0 回に変わり得る)。
    """
    events = _drain_events(server)
    closed_events = [e for e in events if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    stream_closed_events = [e for e in events if e.type == h3.EventType.STREAM_CLOSED]
    assert len(stream_closed_events) == 1
    assert server.get_session_ids() == []
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)


def test_pre_accept_wt_close_session_fin_same_read() -> None:
    """受理前の WT_CLOSE_SESSION が FIN と同一読み取りで届き、accept_session 中に処理されても SessionClosed が 1 回だけ発火することを確認

    準拠クライアントは WT_CLOSE_SESSION 直後に FIN を送る
    (draft-ietf-webtrans-http3-16 Section 6 の MUST)。FIN と同一読み取りで
    届くため受理前 FIN 検知が成立し、移行処理 (pre_accept_fin_accepted_
    session_ids_ への移行) が confirm の前に実行された状態で、confirm の
    処理中にカプセルが処理される (破棄記録条件 1 の経路)。受理前のカプセル
    は nghttp3 の inq にバッファされ、accept_session の confirm 処理中に
    process_blocked_wt_stream_data 経由で同期処理される (nghttp3 の実装
    順序に依存する経路であり、本テストでピン留めする)。2xx が破棄される
    ためクライアント側で SESSION_READY は発火しないことも検証する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # クライアントが受理前に WT_CLOSE_SESSION を送出し、FIN と同一読み取り
    # でサーバーに届く構成 (準拠クライアントの送出順)
    wt_close_data = _send_pre_accept_wt_close_session(client)
    server.receive_stream_data(0, wt_close_data, True)

    # 受理する。confirm 処理中にバッファされた WT_CLOSE_SESSION が処理され、
    # 終了済みセッションの未送信 2xx が破棄される
    assert server.accept_session(0) is True

    # SessionClosed 1 回 / STREAM_CLOSED 1 回 / ID 残留なし / 2xx 不出力
    _assert_wt_close_session_discarded(server)

    # 2xx が破棄されたため、クライアントはセッション確立を認識しない
    # (SESSION_READY が発火しない)
    ready_events = [e for e in _drain_events(client) if e.type == h3.EventType.SESSION_READY]
    assert len(ready_events) == 0

    # 終了を学習したセッション ID 宛の送信が拒否される
    server.send_datagram(0, b"blocked")
    assert server.get_datagrams_to_send() == []
    assert server.open_stream(0, 4, False) is False


def test_pre_accept_wt_close_session_fin_late() -> None:
    """受理前の WT_CLOSE_SESSION が FIN より先に届き、accept_session 中に処理されても未送信 2xx が破棄されることを確認

    カプセルと FIN が別の QUIC パケットで届くと、FIN 検知前に accept_session
    が実行され pre_accept_fin_accepted_session_ids_ のメンバーシップが成立
    しない。accept_session の confirm 処理中に発火した recv_wt_close_session_cb
    を破棄記録の対象に加える拡大の検証である (破棄記録条件 2 の経路)。
    accept_session 後の空 FIN は閉じられたストリームへの読み取りとなり、
    nghttp3 がエラーを返して Error イベントが積まれ得る (実サーバーでは
    無視されるため断言しない)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # クライアントが受理前に WT_CLOSE_SESSION のみを送出する (FIN なし)
    wt_close_data = _send_pre_accept_wt_close_session(client)
    server.receive_stream_data(0, wt_close_data, False)

    # 受理する。confirm 処理中にバッファされた WT_CLOSE_SESSION が処理され、
    # 未送信の 2xx が破棄される (破棄記録条件の拡大)
    assert server.accept_session(0) is True

    # 遅れて届いた空 FIN を渡す (閉じられたストリームへの読み取り)。
    # 現在の依存 nghttp3 では ERR_H3_FRAME_UNEXPECTED の Error イベントが
    # 積まれる (閉じられたストリームへの読み取りは nghttp3 の実装依存で
    # あり、実サーバーでは無視されるため断言しない)
    server.receive_stream_data(0, b"", True)

    # SessionClosed 1 回 / STREAM_CLOSED 1 回 / ID 残留なし / 2xx 不出力
    _assert_wt_close_session_discarded(server)


def test_pre_accept_wt_close_session_no_fin() -> None:
    """受理前 FIN を伴わない WT_CLOSE_SESSION のみの変種で送信窓が閉じられ 2xx が破棄されることを確認

    受理前 FIN なしの変種では移行処理 (pre_accept_fin_accepted_session_ids_
    への挿入) が機能せず、破棄記録は confirm 処理中の発火 (破棄記録条件 2)
    で成立する。再挿入抑止により session_ids_ に ID が残留せず、
    draft-ietf-webtrans-http3-16 Section 6 の MUST (終了を学習したセッション
    ID 宛の新しいデータグラム・ストリームの禁止) の窓が塞がる。fin_late
    変種との違いは FIN が届かないこと (fin_late は届いた空 FIN の処理後の
    挙動を確認する) と、窓の閉塞を直接検証することである。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # クライアントが受理前に WT_CLOSE_SESSION のみを送出する (FIN なし)
    wt_close_data = _send_pre_accept_wt_close_session(client)
    server.receive_stream_data(0, wt_close_data, False)
    assert server.accept_session(0) is True

    # SessionClosed 1 回 / STREAM_CLOSED 1 回 / ID 残留なし / 2xx 不出力
    _assert_wt_close_session_discarded(server)

    # 終了を学習したセッション ID 宛の送信が拒否される (窓の閉塞)
    server.send_datagram(0, b"blocked")
    assert server.get_datagrams_to_send() == []
    assert server.open_stream(0, 4, False) is False


def test_pre_accept_fin_wt_close_session_other_session_unaffected() -> None:
    """遅延クローズ保留中の一方のセッション終了が他方の 2xx 送出に影響しないことを確認

    複数セッションが遅延クローズ保留中の場合、WT_CLOSE_SESSION を受信した
    セッションの 2xx だけが破棄され、他セッションの 2xx は従来どおり
    書き出される (保留集合はセッション ID 単位で処理されることの回帰ピン)。
    両セッションとも遅延クローズの後始末として SessionClosed が発火する
    (セッション 0 は WT_CLOSE_SESSION 受信経由、セッション 4 は 2xx 書き出し
    完了後の遅延クローズ経由)。
    """
    client, server = _create_session_pair()

    # 2 つのセッションを受理前 FIN 付きで確立し、両方の遅延クローズを保留する
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, True)  # 受理前 FIN
    assert server.accept_session(0) is True

    assert client.connect(4, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 4)
    server.receive_stream_data(4, headers, True)  # 受理前 FIN
    assert server.accept_session(4) is True

    server.block_stream(0)
    server.block_stream(4)
    streams = server.get_streams_to_send()
    assert all(stream_id not in (0, 4) for stream_id, _data, _fin in streams)
    assert server.get_session_ids() == [0, 4]

    # セッション 0 に WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(0, 0)
    _pump(client, server)

    # ブロック解除すると、終了したセッション 0 の 2xx は破棄され、
    # 生存セッション 4 の 2xx は書き出される
    assert server.unblock_stream(0) is True
    assert server.unblock_stream(4) is True
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)
    assert any(stream_id == 4 for stream_id, _data, _fin in streams)

    # 両セッションで SessionClosed が発火する: セッション 0 は
    # WT_CLOSE_SESSION 受信経由、セッション 4 は遅延クローズ (2xx 書き出し
    # 完了後) 経由の正常な後始末
    events = _drain_events(server)
    closed_events = [e for e in events if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 2
    assert {e.session_id for e in closed_events} == {0, 4}


def test_pre_accept_wt_close_session_other_session_unaffected() -> None:
    """受理前 WT_CLOSE_SESSION の accept 経路で終了したセッションが他セッションの生存に影響しないことを確認

    生存セッション 4 (通常確立済み) が存在する状態で、セッション 0 が
    受理前 WT_CLOSE_SESSION により accept 経路で終了しても、セッション 4 の
    session_ids_ エントリは残り send_datagram も成功する。accept_session 内の
    discard_stale_2xx の全走査 (他セッションの保留エントリも破棄する) が
    他セッションの送信に波及しないことの回帰ピン。
    """
    client, server = _create_session_pair()

    # セッション 4 を通常確立する
    assert client.connect(4, "https://localhost/webtransport") is True
    _pump(client, server)
    _accept_session(server)
    _pump(server, client)
    assert server.get_session_ids() == [4]

    # セッション 0 に受理前 WT_CLOSE_SESSION を送る (FIN なし。accept 経路)
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)
    wt_close_data = _send_pre_accept_wt_close_session(client)
    server.receive_stream_data(0, wt_close_data, False)
    assert server.accept_session(0) is True

    # セッション 0 だけが終了し、セッション 4 は生存する (ID も送信も維持)
    events = _drain_events(server)
    closed_events = [e for e in events if e.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert server.get_session_ids() == [4]
    server.send_datagram(4, b"alive")
    assert len(server.get_datagrams_to_send()) == 1

    # 終了したセッション 0 の 2xx は送出されない
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)
