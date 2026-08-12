"""WebTransport over HTTP/3 の QPACK デコードブロック中の受理前 FIN テスト

QPACK デコードブロック中に届いた受理前 FIN (サーバーが応答を送信する前に
CONNECT ストリームが FIN で閉じられた) の fin が喪失し、以後どの経路でも
セッション終了が検知されない問題の修正を検証する。ヘッダーが QPACK デコード
ブロック中に fin 付きデータが届くと、nghttp3 はデータを inq にバッファし、
ブロック解除後の再処理で READ_EOF を fin として伝播するが、ヘッダー完了後の
「Server has not submitted response」分岐で WT_SESSION_BLOCKED を立てて
早期 return するため end_stream コールバックに到達せず fin が喪失する。
receive_stream_data の fin 引数による保留記録と、ブロック解除後の CONNECT
判定による移行で検知する。
"""

from __future__ import annotations

import pytest
from conftest import _accept_session, _create_session_pair, _pump

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


def _create_qpack_blocked_setup() -> tuple[h3.Session, h3.Session, bytes, list[bytes]]:
    """QPACK エンコーダーストリーム未到着 (デコードブロック中) のペアを作成する

    クライアントの CONNECT ヘッダーを取得し、QPACK エンコーダーストリーム
    (6) のデータを保留して返す。制御ストリーム (2) と QPACK デコーダー
    ストリーム (10) はサーバーに渡し済みの状態にする。get_streams_to_send
    は 1 回の呼び出しで全てのデータを返すとは限らないため、データが無く
    なるまでループして収集する (conftest.py の _setup_connect と同じ方針)。
    エンコーダーストリームのデータはストリーム種別ヘッダー (0x02) を含めて
    分割され得るため、リストで返す。

    @return (クライアント, サーバー, CONNECT ヘッダー, エンコーダーストリームデータ)
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True

    headers_parts: list[bytes] = []
    encoder_parts: list[bytes] = []
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, fin in streams:
            if stream_id == 0:
                headers_parts.append(data)
            elif stream_id == 6:
                encoder_parts.append(data)
            else:
                server.receive_stream_data(stream_id, data, fin)
    headers = b"".join(headers_parts)
    assert headers, "CONNECT ヘッダーが取得できません"
    assert encoder_parts, "QPACK エンコーダーストリームのデータが取得できません"
    return client, server, headers, encoder_parts


@pytest.mark.parametrize(
    "same_read",
    [True, False],
    ids=["same_read", "separate_read"],
)
def test_qpack_blocked_pre_accept_fin_closes_session(same_read: bool) -> None:
    """QPACK デコードブロック中の受理前 FIN でセッション終了が検知されることを確認

    ブロック中にヘッダー + FIN を同一読み取りで渡すケースと、ヘッダー → 空 FIN
    を別読み取りで渡すケースの両方で、fin が喪失せず、accept_session による
    受理と 2xx レスポンスの書き出し完了後に close_stream で後始末されて
    SessionClosed が error_code 0 で発火する。本テストは修正前実装では失敗
    する (fin が喪失してセッションが終了検知されず session_ids_ に残る)。
    なお、新規検知と既存検知の排他条件 (session_ids_ の count == 0 と
    count > 0) が壊れて二重記録されても、保留集合の冪等性と close_stream
    の二重発火ガードで吸収され観測可能な挙動は変わらないため、排他条件の
    破壊は本テストでは検出できない (設計上の検出限界)。
    """
    client, server, headers, encoder_parts = _create_qpack_blocked_setup()

    # ブロック中にヘッダー + FIN を渡す (同一読み取りなら同時に、別読み取り
    # ならヘッダーの後に空 FIN。空 FIN はバッファされず READ_EOF として
    # 保存される)
    if same_read:
        server.receive_stream_data(0, headers, True)
    else:
        server.receive_stream_data(0, headers, False)
        server.receive_stream_data(0, b"", True)
    assert server.get_session_ids() == []

    # ブロック解除 (QPACK エンコーダーストリーム到着) でヘッダーがデコードされ、
    # SESSION_READY が発火する (fin は喪失せず保留集合に記録済み)
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1
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
    ready_events = [
        event for event in _drain_events(client) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # セッションが終了し、SessionClosed が error_code 0 で発火する
    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert closed_events[0].error_code == 0


def test_qpack_blocked_pre_accept_fin_multiple_sessions() -> None:
    """複数セッションが同時に QPACK ブロック中 FIN を送っても両方検知されることを確認

    移行処理は保留集合を走査して session_ids_ に挿入済みのストリームを
    移行する。2 セッションが同時にブロック解除された場合も、それぞれが
    正しく移行・後始末され、SessionClosed が各セッションで 1 回ずつ発火
    する (イテレータの erase 処理の回帰ピン)。
    """
    client, server = _create_session_pair()

    # 2 つの CONNECT を送信し、両方のヘッダーを QPACK ブロック中に届ける
    assert client.connect(0, "https://localhost/webtransport") is True
    assert client.connect(4, "https://localhost/webtransport") is True
    headers_by_stream: dict[int, bytes] = {}
    encoder_parts: list[bytes] = []
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, fin in streams:
            if stream_id in (0, 4):
                headers_by_stream[stream_id] = headers_by_stream.get(stream_id, b"") + data
            elif stream_id == 6:
                encoder_parts.append(data)
            else:
                server.receive_stream_data(stream_id, data, fin)
    assert set(headers_by_stream) == {0, 4}

    # 両方のセッションをブロック中に FIN 付きで届ける
    for stream_id in (0, 4):
        server.receive_stream_data(stream_id, headers_by_stream[stream_id], True)
    assert server.get_session_ids() == []

    # ブロック解除で両セッションがデコードされる
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 2
    assert {event.session_id for event in ready_events} == {0, 4}

    # 両方のセッションを受理して 2xx を書き出すと、それぞれ後始末される
    for event in ready_events:
        assert server.accept_session(event.session_id) is True
    streams = server.get_streams_to_send()
    assert {stream_id for stream_id, _data, _fin in streams if stream_id in (0, 4)} == {0, 4}

    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 2
    assert {event.session_id for event in closed_events} == {0, 4}
    assert all(event.error_code == 0 for event in closed_events)


def test_qpack_blocked_pre_accept_fin_origin_rejected() -> None:
    """Origin 検証失敗 (403 拒否) のセッションは保留記録が除去されることを確認

    QPACK ブロック中に fin が届いて保留記録されたストリームが Origin 検証
    失敗で 403 拒否された場合、end_headers_cb の拒否分岐で記録が除去される。
    拒否されたセッションは SessionClosed も発火せず session_ids_ にも残ら
    ない (reject 経路の既存の挙動)。保留記録の除去はテスト専用の
    _has_pending_qpack_blocked_fin_stream で直接確認する (除去されない場合
    は公開 API の挙動に現れないため)。
    """
    # 許可オリジンを設定したサーバーを用意する (conftest のヘルパーは
    # allowed_origins を設定できないため手動で構築する)
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server_config.allowed_origins = ["https://allowed.example"]
    server = h3.Session.create_server(server_config)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)
    _pump(server, client)  # サーバーの SETTINGS をクライアントへ

    # 許可外 Origin の CONNECT を QPACK ブロック中に fin 付きで届ける
    assert client.connect(0, "https://localhost/webtransport", "https://other.example") is True
    headers = None
    encoder_parts: list[bytes] = []
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, fin in streams:
            if stream_id == 0:
                headers = (headers or b"") + data
            elif stream_id == 6:
                encoder_parts.append(data)
            else:
                server.receive_stream_data(stream_id, data, fin)
    assert headers is not None

    # ブロック中に FIN 付きで届き、保留記録される
    server.receive_stream_data(0, headers, True)
    assert server._has_pending_qpack_blocked_fin_stream(0) is True

    # ブロック解除で 403 拒否され、保留記録が除去される
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    assert server._has_pending_qpack_blocked_fin_stream(0) is None
    assert server.get_session_ids() == []
    assert all(event.type != h3.EventType.SESSION_CLOSED for event in _drain_events(server))


def test_qpack_blocked_pre_accept_fin_reset_removes_record() -> None:
    """ブロック中にリセットされたストリームの保留記録が除去されることを確認

    ブロック中に fin で保留記録されたストリームが、ブロック解除前に
    close_stream (リセット) された場合、end_headers_cb は発火せず移行条件
    (session_ids_ への挿入) も成立しない。close_stream での除去がなければ
    記録が接続終了まで残留するため、除去されることをテスト専用の
    _has_pending_qpack_blocked_fin_stream で直接確認する。
    """
    _client, server, headers, encoder_parts = _create_qpack_blocked_setup()

    # ブロック中にヘッダー + FIN を渡し、保留記録される
    server.receive_stream_data(0, headers, True)
    assert server._has_pending_qpack_blocked_fin_stream(0) is True

    # ブロック解除前に close_stream でリセットすると記録が除去される
    server.close_stream(0, 0)
    assert server._has_pending_qpack_blocked_fin_stream(0) is None

    # ブロック解除してもセッションは確立されない (ストリームは閉じている)
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    assert server.get_session_ids() == []
    assert all(event.type != h3.EventType.SESSION_CLOSED for event in _drain_events(server))


def test_qpack_blocked_normal_session_unaffected() -> None:
    """QPACK ブロック中の通常のセッション確立 (FIN なし) が影響を受けないことを確認

    ブロック中にヘッダー (FIN なし) が届いても保留記録されず、ブロック解除後に
    通常どおりセッションが確立されて SessionClosed も発火しない。
    """
    _client, server, headers, encoder_parts = _create_qpack_blocked_setup()
    server.receive_stream_data(0, headers, False)  # FIN なし

    # ブロック解除後にヘッダーがデコードされ、通常どおり受理できる
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    _accept_session(server)
    server.get_streams_to_send()

    # セッションが確立されたまま残り、SessionClosed は発火しない
    assert server.get_session_ids() == [0]
    assert all(event.type != h3.EventType.SESSION_CLOSED for event in _drain_events(server))
