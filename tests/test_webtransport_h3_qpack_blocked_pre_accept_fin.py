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
from conftest import (
    _accept_session,
    _create_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_varint,
    _pump,
)

from webtransport import h3


def _encode_h3_data_frame(payload: bytes) -> bytes:
    """H3 DATA フレーム (Type 0x00) のワイヤバイト列を組み立てる"""
    return _encode_varint(0x00) + _encode_varint(len(payload)) + payload


def _encode_wt_close_session_capsule(error_code: int, message: bytes) -> bytes:
    """WT_CLOSE_SESSION capsule (Type 0x2843) のワイヤバイト列を組み立てる"""
    return _encode_capsule(0x2843, error_code.to_bytes(4, "big") + message)


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


@pytest.mark.parametrize(
    "same_read",
    [True, False],
    ids=["same_read", "separate_read"],
)
def test_qpack_blocked_pipelined_data_then_fin_closes_session(same_read: bool) -> None:
    """QPACK ブロック中の HEADERS + DATA + FIN がクラッシュせず終了検知されることを確認

    正当なワイヤ列 (HEADERS フレーム + 空 DATA フレーム + FIN) が QPACK
    デコードブロック中に届いても、サーバーが SIGABRT せずハングもしない。
    ブロック解除後に保持データが投入され、受理前 FIN として SessionClosed
    が error_code 0 で 1 回だけ発火する (ブロックなしの同一ワイヤ列と同一)。
    """
    client, server, headers, encoder_parts = _create_qpack_blocked_setup()

    # 空 DATA フレームを後続させる (カプセルなしのため終了検知は FIN 経路のみ)
    pipelined = _encode_h3_data_frame(b"")

    # ブロック中に HEADERS + DATA (+ FIN) を渡す (ここで旧実装は SIGABRT した)
    if same_read:
        server.receive_stream_data(0, headers + pipelined, True)
    else:
        server.receive_stream_data(0, headers, False)
        server.receive_stream_data(0, pipelined, True)
    # 接続エラーにならず生存している (クラッシュせずに戻ったことの確認)
    assert server.is_closed() is False

    # ブロック解除で SESSION_READY が 1 回だけ発火する
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1
    assert server.get_session_ids() == [0]

    # 受理して 2xx を書き出すと遅延クローズで後始末される
    assert server.accept_session(0) is True
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)

    # 書き出した 2xx をクライアントに渡すと確立が認識される
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)
    assert len([e for e in _drain_events(client) if e.type == h3.EventType.SESSION_READY]) == 1

    # SessionClosed が error_code 0 で 1 回だけ発火し、二重発火しない
    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert closed_events[0].error_code == 0


@pytest.mark.parametrize(
    "same_read",
    [True, False],
    ids=["same_read", "separate_read"],
)
def test_qpack_blocked_pipelined_wtclose_then_fin_closes_once(same_read: bool) -> None:
    """QPACK ブロック中の HEADERS + WT_CLOSE_SESSION + FIN で二重終了しないことを確認

    受理前にバッファされた WT_CLOSE_SESSION は confirm 時に処理され、
    SessionClosed が 1 回だけ発火する。後続 FIN は終了済みのため無視され、
    二重に SessionClosed は発火しない (ブロックなしと同一)。
    """
    client, server, headers, encoder_parts = _create_qpack_blocked_setup()

    # 正常な WT_CLOSE_SESSION (error_code 0、空メッセージ) を DATA 化する
    capsule = _encode_wt_close_session_capsule(0, b"")
    pipelined = _encode_h3_data_frame(capsule)

    # ブロック中に HEADERS + WT_CLOSE_SESSION (+ FIN) を渡す
    if same_read:
        server.receive_stream_data(0, headers + pipelined, True)
    else:
        server.receive_stream_data(0, headers, False)
        server.receive_stream_data(0, pipelined, True)
    assert server.is_closed() is False

    # ブロック解除で SESSION_READY が発火する
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # 受理する。confirm 時にバッファされた WT_CLOSE_SESSION が処理される
    assert server.accept_session(0) is True

    # SessionClosed が 1 回だけ発火し、未送信 2xx は破棄される
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert server.get_session_ids() == []
    streams = server.get_streams_to_send()
    assert all(stream_id != 0 for stream_id, _data, _fin in streams)


@pytest.mark.parametrize(
    "same_read",
    [True, False],
    ids=["same_read", "separate_read"],
)
def test_qpack_blocked_pipelined_wtclose_no_fin_closes_once(same_read: bool) -> None:
    """QPACK ブロック中の HEADERS + WT_CLOSE_SESSION (FIN なし) で終了することを確認

    FIN なし変種でも、ブロック解除後の保持データ投入と confirm 時の処理で
    SessionClosed が 1 回だけ発火する。FIN 検知経路 (受理前 FIN) とは独立に
    WT_CLOSE_SESSION 経路で終了するため、FIN の有無で挙動が変わらない。
    """
    _client, server, headers, encoder_parts = _create_qpack_blocked_setup()

    # FIN なしの WT_CLOSE_SESSION を DATA 化する
    capsule = _encode_wt_close_session_capsule(0, b"")
    pipelined = _encode_h3_data_frame(capsule)

    # ブロック中に HEADERS + WT_CLOSE_SESSION (FIN なし) を渡す
    if same_read:
        server.receive_stream_data(0, headers + pipelined, False)
    else:
        server.receive_stream_data(0, headers, False)
        server.receive_stream_data(0, pipelined, False)
    assert server.is_closed() is False

    # ブロック解除で SESSION_READY が発火する
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # 受理する。confirm 時に WT_CLOSE_SESSION が処理される
    assert server.accept_session(0) is True

    # SessionClosed が 1 回だけ発火する
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0
    assert server.get_session_ids() == []


def test_qpack_blocked_split_frame_header_then_data_fin() -> None:
    """HEADERS フレームヘッダー分割到着でもクラッシュせず終了検知されることを確認

    フレーム種別・長さの varint が STREAM_DATA イベントを跨いで分割される
    ケースに備える。先頭 1 バイトだけを先に渡し、残り HEADERS + 空 DATA +
    FIN を後続で渡しても、バインディング側の解釈状態で境界を復元し、
    nghttp3 には HEADERS のみが渡る。ブロック解除後は通常の受理前 FIN と
    同じく SessionClosed が error_code 0 で 1 回だけ発火する。
    """
    client, server, headers, encoder_parts = _create_qpack_blocked_setup()
    assert len(headers) >= 2, "CONNECT ヘッダーが短すぎます"

    pipelined = _encode_h3_data_frame(b"")

    # 先頭 1 バイトだけを先に渡す (フレームヘッダー不完全のため保持される)
    server.receive_stream_data(0, headers[:1], False)
    # 残り HEADERS + DATA + FIN を渡す
    server.receive_stream_data(0, headers[1:] + pipelined, True)
    assert server.is_closed() is False

    # ブロック解除で SESSION_READY が発火する
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # 受理して 2xx を書き出すと遅延クローズで後始末される
    assert server.accept_session(0) is True
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)

    # SessionClosed が 1 回だけ発火する
    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 0


def test_qpack_blocked_split_headers_payload_then_data_fin() -> None:
    """HEADERS ペイロード途中分割でもクラッシュせず終了検知されることを確認

    QUIC の STREAM_DATA 分割は任意位置で起こるため、HEADERS フレームの
    フィールドセクション途中で分割され、後半に DATA + FIN が続く経路も
    到達可能である。前半は HEADERS の継続として転送され、後半は境界で分割
    して保持される。ブロック解除後は通常の受理前 FIN と同じく
    SessionClosed が error_code 0 で 1 回だけ発火する。
    """
    client, server, headers, encoder_parts = _create_qpack_blocked_setup()
    assert len(headers) >= 4, "CONNECT ヘッダーが短すぎます"

    # HEADERS をフィールドセクション途中で分割する
    split_at = len(headers) // 2
    pipelined = _encode_h3_data_frame(b"")

    # 前半 (HEADERS の継続) を渡す
    server.receive_stream_data(0, headers[:split_at], False)
    # 後半 HEADERS + DATA + FIN を渡す
    server.receive_stream_data(0, headers[split_at:] + pipelined, True)
    assert server.is_closed() is False

    # ブロック解除で SESSION_READY が発火する
    for data in encoder_parts:
        server.receive_stream_data(6, data, False)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # 受理して 2xx を書き出すと遅延クローズで後始末される
    assert server.accept_session(0) is True
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 for stream_id, _data, _fin in streams)
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)

    # SessionClosed が 1 回だけ発火する
    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 0


def test_qpack_blocked_pipelined_data_multiple_sessions() -> None:
    """複数セッションの同時パイプラインでも各セッションが 1 回ずつ終了することを確認

    2 セッションが同時に QPACK ブロック中 DATA + FIN を pipeline して解除
    されると、保持投入が複数ストリームで連続実行される。各セッションの
    SessionClosed が 1 回ずつ (計 2 回) 発火し、二重発火や取りこぼしがない。
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

    # 両方のセッションをブロック中に HEADERS + DATA + FIN で届ける
    pipelined = _encode_h3_data_frame(b"")
    for stream_id in (0, 4):
        server.receive_stream_data(stream_id, headers_by_stream[stream_id] + pipelined, True)
    assert server.is_closed() is False

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
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)

    # 各セッションで SessionClosed が 1 回ずつ発火する
    assert server.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 2
    assert {event.session_id for event in closed_events} == {0, 4}
    assert all(event.error_code == 0 for event in closed_events)


def test_qpack_guard_incomplete_headers_with_fin_is_connection_error() -> None:
    """FIN 付き不完全 HEADERS 到着が接続エラーとして扱われることを確認

    フレームヘッダー解釈に足りない 1 バイトだけが FIN 付きで届いた場合、
    ガードは蓄積せず nghttp3 に判定を委ねる。不完全な HEADERS フレームの
    終端はプロトコルエラーであり、接続エラーとして closed_ になる。
    """
    _client, server, _headers, _encoder_parts = _create_qpack_blocked_setup()

    # 種別のみ (長さ varint なし) の 1 バイトを FIN 付きで渡す
    ret = server.receive_stream_data(4, b"\x01", True)

    # nghttp3 が負値を返し、接続エラーとして処理される
    assert ret == 0
    assert server.is_closed() is True
    assert any(event.type == h3.EventType.ERROR for event in _drain_events(server))


def test_qpack_guard_non_headers_first_frame_passes_through() -> None:
    """先頭が HEADERS でない新規ストリームが素通しされることを確認

    WebTransport データストリーム (先頭がストリーム種別ヘッダー) は
    ガードの保持対象外であり、一括で nghttp3 へ渡される。以後の読み取りも
    素通しされ、セッション確立や接続状態に影響しない。
    """
    _client, server, _headers, _encoder_parts = _create_qpack_blocked_setup()

    # WT 双方向データストリームの先頭 (種別 0x41 + セッション ID + 欠片)
    first = b"\x40\x41" + _encode_varint(0) + b"hello"

    # 1 回目の読み取り (先頭解釈で非 HEADERS 確定) と 2 回目の読み取り
    # (素通し) のどちらでも異常にならない
    server.receive_stream_data(4, first, False)
    assert server.is_closed() is False
    server.receive_stream_data(4, b"world", False)
    assert server.is_closed() is False

    # セッションは確立されず、イベントも発火しない
    assert server.get_session_ids() == []
    assert _drain_events(server) == []


def test_qpack_guard_huge_headers_length_rejected_gracefully() -> None:
    """巨大な HEADERS 長さ宣言が優雅に拒否されることを確認

    Length に varint 最大級の値を宣言した HEADERS は、到着済み全量が
    HEADERS 範囲内として転送される (安全側への倒し方)。nghttp3 側で
    復元不能として接続エラーになり、Error イベントとともに closed_ になる。
    クラッシュもハングもせず、既存の負値処理経路で扱われる。
    """
    _client, server, _headers, _encoder_parts = _create_qpack_blocked_setup()

    # HEADERS (0x01) + 8 バイト varint の巨大長 + 少量ペイロード
    huge_length = (0xC000000000000000 | ((1 << 62) - 1)).to_bytes(8, "big")
    data = b"\x01" + huge_length + b"\x00" * 10

    ret = server.receive_stream_data(4, data, False)

    # nghttp3 が負値を返し、接続エラーとして処理される
    assert ret == 0
    assert server.is_closed() is True
    assert any(event.type == h3.EventType.ERROR for event in _drain_events(server))
