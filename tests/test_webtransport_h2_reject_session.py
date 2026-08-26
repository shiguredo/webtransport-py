"""WebTransport over HTTP/2 クライアントの非 2xx 応答受信時のセッション ID 削除テスト

サーバーが reject_session (非 2xx 応答) で CONNECT リクエストを拒否しても、
クライアントの wt_sessions_ にエントリが残り続け、拒否されたセッション ID
宛の send_datagram が DATAGRAM capsule をワイヤへ送出し続ける問題の修正を
検証する。draft-ietf-webtrans-http2-15 Section 3.2 の「A WebTransport
session is established when the server sends a 2xx response」により、非 2xx
で拒否されたセッションは一度も確立されておらず、終了通知 (SessionClosed)
の意味論が合わない。そのため SESSION_REJECTED を push してからセッション
エントリを削除する。

あわせて、サーバー側 reject_session が 2xx 応答でセッション ID を削除しない
こと (2xx 送出 = セッション確立) を検証する。2xx 保持エントリの残留は
両ハーフクローズ時の on_stream_close_callback による SessionClosed 発火で
間接検証する。
"""

from __future__ import annotations

import pytest
from conftest import _create_h2_session_pair, _drain_events, _h2_pump

from webtransport import h2


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Capsule Protocol のカプセルバイト列を組み立てる (RFC 9297 Section 3.2)

    テストで使う小さい値のみ対応する (Type / Length とも 1 バイト varint)。
    HTTP/2 DATA フレームのペイロードはカプセルバイト列そのもののため、
    ワイヤデータに対する部分列チェックで送出を検証できる。DATAGRAM capsule
    の Type は 0x00 である。
    """
    assert capsule_type < 0x40 and len(payload) < 0x40
    return bytes([capsule_type, len(payload)]) + payload


def _encode_status_headers(session_id: int, status_code: int) -> bytes:
    """:status を指定した HEADERS フレームのワイヤバイト列を組み立てる

    HPACK 圧縮済みヘッダーブロックは、静的テーブルの :status (index 8) を
    参照するリテラルヘッダーフィールド (RFC 7541 Section 6.2.2 の
    Literal Header Field without Indexing) で組み立てる。0x08 は 4 ビット
    プレフィックスで index 8 を表し、インクリメンタルインデックスを伴わ
    ないためデコーダーの動的テーブルを汚さない。フレームは END_HEADERS
    フラグ付きの HEADERS フレーム (中間応答や END_STREAM なしの最終応答の
    注入に使う)。
    """
    status = str(status_code).encode()
    header_block = bytes([0x08, len(status)]) + status
    length = len(header_block)
    frame = (
        length.to_bytes(3, "big")
        + bytes([0x01, 0x04])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + header_block
    )
    return frame


def _encode_data_frame(session_id: int, payload: bytes = b"", end_stream: bool = False) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    END_STREAM フラグ (0x01) 付きでピアがストリームを閉じた場合を再現する。
    h2 の公開 API に END_STREAM のみを送出する手段が存在しないため、
    ワイヤ注入で再現する。
    """
    flags = 0x01 if end_stream else 0x00
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_reject_removes_session(status_code: int) -> None:
    """非 2xx 拒否でクライアントの wt_sessions_ からエントリが削除されることを確認

    拒否されたセッションは一度も確立されていない (draft-15 Section 3.2)。
    エントリ削除により send_datagram のガードが成立し、DATAGRAM capsule が
    ワイヤへ送出されない (エントリの削除は公開 API から直接観測できない
    ため、送出抑止で間接検証する)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが非 2xx で拒否する
    server.reject_session(session_id, status_code)
    _h2_pump(server, client)

    # 拒否後の send_datagram はワイヤへ送出されない
    client.send_datagram(session_id, b"after-reject")
    wire = client.send()
    assert wire is None or _encode_capsule(0x00, b"after-reject") not in wire


def test_client_non_2xx_reject_no_session_closed_event() -> None:
    """拒否されたセッションに対して SessionClosed イベントが発火しないことを確認

    非 2xx で拒否されたセッションは一度も確立されていない (draft-15
    Section 3.2) ため、SessionClosed (セッション終了の通知) の意味論が
    合わない。SessionRejected を発火してからエントリを削除する。本テストは
    修正前実装でも通る設計ピンであり、SessionClosed 不発火の意味論を守る。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    # SessionClosed は発火しない
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(client))


def test_client_non_2xx_reject_close_session_noop() -> None:
    """拒否後の close_session が no-op になり SessionClosed が発火しないことを確認

    エントリ削除方式では、拒否後にクライアントが close_session を呼んでも
    エントリ不在で no-op になり、WT_CLOSE_SESSION も END_STREAM も送出され
    ない。is_terminated 方式では close_session がエントリを残したまま
    WT_CLOSE_SESSION + END_STREAM を送出し、ピアが閉じた後に
    on_stream_close_callback 経由で SessionClosed が発火するため、両方式の
    差別化シナリオになる。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    # 拒否後の close_session は no-op (WT_CLOSE_SESSION capsule は送出されない)
    client.close_session(session_id, 0)
    wire = client.send()
    # 0x6843 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint
    assert wire is None or b"\x68\x43" not in wire

    # ピアに何も届かず、両側で SessionClosed が発火しない
    _h2_pump(client, server)
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(server))
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(client))


def test_client_response_201_session_established() -> None:
    """2xx 非 200 応答 (201) でセッションが確立として扱われることを確認

    draft-15 Section 3.2 では 2xx 全般がセッション確立であり、確立条件を
    「200 のみ」から先頭文字が '2' の 2xx 全般へ広げた。201 は確立
    (is_established / SESSION_READY) として扱われることを検証する。
    201 応答は END_STREAM 付きで届くため、確立直後のセッション終了
    (SessionClosed) も正しく検知される (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 201 で応答する (reject_session は任意の status_code で
    # 応答を生成できる。2xx は拒否ではなくセッション確立の意味論)
    server.reject_session(session_id, 201)
    _h2_pump(server, client)

    # 201 は確立として扱われる: SESSION_READY が発火する
    events = _drain_events(client)
    ready_events = [event for event in events if event.type == h2.EventType.SESSION_READY]
    assert len(ready_events) == 1
    assert ready_events[0].session_id == session_id
    # イベント順序 (SESSION_READY → SESSION_CLOSED) のピン: 逆転すると
    # 高レベル Client.connect が SESSION_CLOSED を先に見て False を返す
    assert events[0].type == h2.EventType.SESSION_READY

    # 201 応答の END_STREAM によりセッション終了が正しく検知される
    # (修正前は 201 が確立扱いされず END_STREAM も誤検知されず残留した)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert client.get_session_ids() == []


def test_client_receive_1xx_keeps_session() -> None:
    """1xx 中間応答ではエントリが削除されないことを確認

    nghttp2 は 1xx を NGHTTP2_HCAT_RESPONSE として通知する (nghttp2.h の
    on_begin_headers_callback と nghttp2_headers_category enum の docstring。
    stream state が OPENING のストリームへの最初の HEADERS が RESPONSE に
    分類され、1xx 受信で state が OPENED に遷移するため、続く最終応答は
    NGHTTP2_HCAT_HEADERS で通知される)。1xx は中間応答であり拒否ではない
    (draft-15 Section 3.2 では 2xx のみが確立) ため、削除対象に含めない。
    1xx 後の最終応答がレスポンス処理分岐で捕捉されずエントリが残るのは
    既存の制約であり、1xx を挟んだ拒否は本対応の削除が機能しない。本テスト
    は修正前実装でも通る設計ピンであり、1xx が削除対象に含まれないことを
    守る。h2 の公開 API に 1xx 送出手段が存在しないため、1xx HEADERS
    フレームのワイヤバイト列を直接注入する (without Indexing のリテラルの
    ため動的テーブルは汚さない)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 1xx レスポンスの HEADERS フレームを直接注入する
    ret = client.receive(_encode_status_headers(session_id, 103))
    assert ret > 0, "1xx HEADERS フレームの注入に失敗しました"
    assert not client.is_closed(), "1xx 注入で接続が閉じられました"

    # 1xx では削除されない: DATAGRAM capsule がワイヤに送出され続ける
    client.send_datagram(session_id, b"after-1xx")
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x00, b"after-1xx") in wire


def test_client_receive_1xx_then_final_response_keeps_session() -> None:
    """1xx 中間応答を挟んだ最終応答 (拒否) では削除が機能せずエントリが残ることを確認

    1xx 受信後は stream state が OPENED に遷移するため、続く最終応答は
    NGHTTP2_HCAT_HEADERS で通知され、レスポンス処理分岐 (HCAT_RESPONSE) で
    捕捉されない。1xx を挟んだ拒否は本対応の削除が機能せず、wt_sessions_
    のエントリと pending_headers_ が残って DATAGRAM capsule が送出され続け
    る (既知の制約の回帰ピン)。本テストは修正前実装でも通る設計ピンであり、
    既知の制約の維持を守る。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 1xx レスポンスの HEADERS フレームを直接注入し、続けて 403 の最終
    # 応答をサーバーから届ける
    ret = client.receive(_encode_status_headers(session_id, 103))
    assert ret > 0, "1xx HEADERS フレームの注入に失敗しました"
    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    # 1xx を挟んだ拒否ではエントリが残り、DATAGRAM capsule が送出され続ける
    client.send_datagram(session_id, b"after-1xx-403")
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x00, b"after-1xx-403") in wire


def test_client_accept_200_normal_session_unaffected() -> None:
    """通常のセッション確立 (200 応答) が非 2xx 応答処理の影響を受けないことを確認

    SESSION_READY の発火条件 (2xx 全般 = 先頭文字が '2') は非 2xx 拒否処理の
    追加後も維持される。通常の確立経路の維持を守る回帰ピン。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)
    assert server.accept_session(session_id) is True
    _h2_pump(server, client)

    # クライアントで SESSION_READY が発火し、セッションが確立される
    ready_events = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_READY]
    assert len(ready_events) == 1
    assert ready_events[0].session_id == session_id

    # send_datagram は送出され、ピアに届く
    client.send_datagram(session_id, b"hello")
    _h2_pump(client, server)
    datagram_events = [e for e in _drain_events(server) if e.type == h2.EventType.DATAGRAM]
    assert len(datagram_events) == 1
    assert datagram_events[0].session_id == session_id


def test_client_reject_other_session_unaffected() -> None:
    """一方のセッションを拒否しても他方のセッションが影響を受けないことを確認

    wt_sessions_ からの削除はストリーム ID 単位で行われる。確立済みの
    セッションは拒否されたセッションの影響を受けず、send_datagram の
    DATAGRAM capsule がワイヤに送出され続ける。
    """
    client, server = _create_h2_session_pair()

    # 1 つ目のセッションを確立する
    first_session_id = client.connect("https://localhost/webtransport")
    assert first_session_id >= 0
    _h2_pump(client, server)
    assert server.accept_session(first_session_id) is True
    _h2_pump(server, client)

    # 2 つ目のセッションを拒否する
    second_session_id = client.connect("https://localhost/webtransport")
    assert second_session_id >= 0
    _h2_pump(client, server)
    server.reject_session(second_session_id, 403)
    _h2_pump(server, client)

    # 拒否されたセッションへの送信は送出されない
    client.send_datagram(second_session_id, b"rejected")
    wire = client.send()
    assert wire is None or _encode_capsule(0x00, b"rejected") not in wire

    # 生存セッションへの送信は送出され、ピアに届く
    client.send_datagram(first_session_id, b"alive")
    _h2_pump(client, server)
    datagram_events = [e for e in _drain_events(server) if e.type == h2.EventType.DATAGRAM]
    assert len(datagram_events) == 1
    assert datagram_events[0].session_id == first_session_id


@pytest.mark.parametrize(
    "status_code, expected_closed",
    [(201, True), (403, False)],
    ids=["2xx_kept", "non_2xx_removed"],
)
def test_server_reject_status_code_entry_retention(status_code: int, expected_closed: bool) -> None:
    """reject_session の 2xx 応答時はサーバーのエントリが残り、両ハーフクローズで SessionClosed が発火することを確認

    draft-15 Section 3.2 の「A WebTransport session is established when
    the server sends a 2xx response」により、2xx 送出はセッション確立であり、
    reject_session に 2xx を渡しても wt_sessions_ から削除しない (h3 側の
    reject_session と対称の意味論)。エントリ残留は公開 API から直接観測
    できないため、両ハーフクローズ時の on_stream_close_callback の
    SessionClosed 発火 (エントリ残留時のみ発火) で間接検証する。サーバーは
    拒否時に既に END_STREAM を送出済みのため、クライアント側のハーフクローズ
    (END_STREAM 付き DATA フレームのワイヤ注入) で on_stream_close_callback
    を発火させる。非 2xx (403) ではエントリが削除されるため発火しない。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが reject_session で応答する
    server.reject_session(session_id, status_code)
    _h2_pump(server, client)

    # クライアント側のハーフクローズ (END_STREAM 付き DATA フレーム) を注入する
    ret = server.receive(_encode_data_frame(session_id, end_stream=True))
    assert ret > 0, "END_STREAM フレームの注入に失敗しました"

    # 2xx 保持時のみ SessionClosed が発火する
    closed_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]
    if expected_closed:
        assert len(closed_events) == 1
        assert closed_events[0].session_id == session_id
        assert closed_events[0].error_code == 0
    else:
        assert len(closed_events) == 0


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_reject_pushes_session_rejected_event(
    status_code: int,
) -> None:
    """非 2xx 拒否で SESSION_REJECTED が status_code 付きで発火することを確認

    SessionClosed は一度も確立されていないセッションの終了通知という
    意味論が合わないため発火しない (設計ピン)。拒否の通知としては
    SESSION_REJECTED が発火し、event.session_id が該当セッション、
    event.status_code には受信した HTTP status code が載る (600 以上は
    0 丸めで、その検証はワイヤ注入の
    test_client_non_2xx_overflow_status_rounds_zero_by_wire_injection で
    行う。reject_session は 600 以上を ValueError にするため)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが非 2xx で拒否する
    server.reject_session(session_id, status_code)
    _h2_pump(server, client)

    # SESSION_REJECTED が該当セッションで発火し、他のイベントは発火しない
    events = _drain_events(client)
    assert len(events) == 1
    rejected_event = events[0]
    assert rejected_event.type == h2.EventType.SESSION_REJECTED
    assert rejected_event.session_id == session_id
    expected_status_code = status_code if status_code < 600 else 0
    assert rejected_event.status_code == expected_status_code
    assert rejected_event.headers == []


def test_session_ready_includes_received_headers() -> None:
    """SESSION_READY イベントに受信 HTTP ヘッダーが載ることを確認

    サーバー側には CONNECT リクエストのヘッダー (:method / :scheme /
    :authority / :path / :protocol / origin / webtransport-init) が送信順で、
    クライアント側には 2xx 応答のヘッダー (:status / webtransport-init) が
    載る。順序と各ヘッダーの名前・値を検証する。

    サーバー側の headers は将来の高レベル Origin 検証 API が参照する。
    ただしヘッダー順序は RFC で規定されず、bindings の nva 提出順に依存
    する (意図的なピンである)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport", "https://example.com")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバー側の SESSION_READY に CONNECT リクエストのヘッダーが載る
    server_ready_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_READY]
    assert len(server_ready_events) == 1
    server_headers = server_ready_events[0].headers
    assert server_headers[0] == (":method", "CONNECT")
    assert server_headers[1] == (":scheme", "https")
    assert server_headers[2] == (":authority", "localhost")
    assert server_headers[3] == (":path", "/webtransport")
    assert server_headers[4] == (":protocol", "webtransport")
    assert server_headers[5] == ("origin", "https://example.com")
    assert server_headers[6] == ("webtransport-init", "u=262144, bl=262144, br=262144")
    assert len(server_headers) == 7

    # サーバーが受理すると、クライアント側の SESSION_READY に応答ヘッダーが載る
    assert server.accept_session(session_id) is True
    _h2_pump(server, client)

    client_ready_events = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_READY]
    assert len(client_ready_events) == 1
    client_headers = client_ready_events[0].headers
    assert client_headers[0] == (":status", "200")
    assert client_headers[1] == ("webtransport-init", "u=262144, bl=262144, br=262144")
    assert len(client_headers) == 2


def test_session_ready_status_code_stays_default() -> None:
    """SESSION_READY イベントの status_code がデフォルト値のままであることを確認

    status_code は SessionRejected 発火時のみ意味を持ち、他のイベントでは
    デフォルト値 (0) のままである。SessionReady では headers のみ意味を
    持ち、status_code が 0 であることを検証する。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)
    assert server.accept_session(session_id) is True
    _h2_pump(server, client)

    # クライアント側の SESSION_READY の status_code はデフォルト 0
    ready_events = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_READY]
    assert len(ready_events) == 1
    assert ready_events[0].status_code == 0

    # サーバー側の SESSION_READY の status_code もデフォルト 0
    server_ready_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_READY]
    assert len(server_ready_events) == 1
    assert server_ready_events[0].status_code == 0

    # SessionRejected 以外のイベント (StreamData) でもデフォルトのまま
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"hello")
    _h2_pump(client, server)
    stream_events = [e for e in _drain_events(server) if e.type == h2.EventType.STREAM_DATA]
    assert len(stream_events) == 1
    assert stream_events[0].status_code == 0
    assert stream_events[0].headers == []


def test_client_response_201_without_end_stream_keeps_session() -> None:
    """END_STREAM なしの 201 応答でセッションが確立として機能することを確認

    reject_session(201) は END_STREAM 付きで応答するため (確立直後に終了する
    シナリオ)、確立後にセッションとして機能する挙動はワイヤ注入 (END_STREAM
    なしの 2xx HEADERS) で検証する。draft-15 Section 3.2 の 2xx 全般確立に
    より SESSION_READY が発火し、open_stream / send_datagram が機能する
    (修正前は 201 を非確立として扱い、高レベル Client.connect がハング
    していた)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # END_STREAM なしの 201 HEADERS をワイヤ注入する
    ret = client.receive(_encode_status_headers(session_id, 201))
    assert ret > 0, "201 HEADERS フレームの注入に失敗しました"

    # 確立イベントが発火する
    events = _drain_events(client)
    ready_events = [event for event in events if event.type == h2.EventType.SESSION_READY]
    assert len(ready_events) == 1
    assert ready_events[0].session_id == session_id

    # 確立後のセッション機能 (ストリーム開設とデータグラム送出) が動作する
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_datagram(session_id, b"alive")
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x00, b"alive") in wire


def test_client_non_2xx_overflow_status_rounds_zero_by_wire_injection() -> None:
    """ワイヤ注入の 600 以上 :status で SESSION_REJECTED の status_code が 0 に丸められることを確認

    reject_session は範囲外 (200-599 以外) を ValueError にするため、
    600 以上の :status を受信した場合の 0 丸めはワイヤ注入で検証する
    (HTTP status code として意味を持たない値をアプリへ渡さない)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 600 のレスポンス HEADERS をワイヤ注入する
    ret = client.receive(_encode_status_headers(session_id, 600))
    assert ret > 0, "600 HEADERS フレームの注入に失敗しました"

    events = _drain_events(client)
    rejected_events = [event for event in events if event.type == h2.EventType.SESSION_REJECTED]
    assert len(rejected_events) == 1
    assert rejected_events[0].status_code == 0


def test_server_reject_session_invalid_status_code_raises_value_error() -> None:
    """範囲外の status_code で reject_session が ValueError を投げることを確認

    reject_session は 200-599 以外 (1xx・3 桁未満・4 桁以上・600 以上) を
    ValueError にする (誤用パスで「SessionClosed 非発火」の設計ピンを
    破らせない)。例外は副作用の前に投げられるため、ワイヤにも :status は
    送出されない。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    for invalid in (99, 100, 101, 199, 600, 999):
        with pytest.raises(ValueError):
            server.reject_session(session_id, invalid)

    # 例外時はワイヤに :status は送出されない (send() に送信物が残らない)
    wire = server.send()
    assert wire is None or b":status" not in wire


def test_server_reject_session_valid_status_code_delivered() -> None:
    """許容範囲内の status_code (200-599) で reject_session が送出されることを確認

    403 等の拒否コードはワイヤに送出され、クライアントで SESSION_REJECTED
    として受信される (既存挙動の回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    events = _drain_events(client)
    rejected_events = [event for event in events if event.type == h2.EventType.SESSION_REJECTED]
    assert len(rejected_events) == 1
    assert rejected_events[0].status_code == 403
