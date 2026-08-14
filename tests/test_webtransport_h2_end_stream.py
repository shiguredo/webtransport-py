"""WebTransport over HTTP/2 の END_STREAM によるセッション終了検知テスト

ピアが WT_CLOSE_SESSION カプセルを送らず END_STREAM フレームのみで CONNECT
ストリームを閉じた場合 (draft-ietf-webtrans-http2-15 Section 3.4 の正規の
終了経路) にセッション終了を検知する修正を検証する。WT_CLOSE_SESSION なし
のクリーンクローズは error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION
と等価 (Section 6.12)。

あわせて、WT_CLOSE_SESSION 受信後の挙動 (Section 6.12 の受信者 MUST である
END_STREAM 応答の送出と、受信後の close_session / send_stream_data が
塞がれて SessionClosed が二重発火しないこと) を検証する。
"""

from __future__ import annotations

from conftest import _connect_h2_session, _create_h2_session_pair, _drain_events, _h2_pump

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


def _encode_data_frame(session_id: int, payload: bytes = b"", end_stream: bool = False) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    END_STREAM フラグ (0x01) 付きでピアがストリームを閉じた場合を再現する。
    h2 の公開 API に WT_CLOSE_SESSION なしで END_STREAM のみを送出する手段が
    存在しないため、ワイヤ注入で再現する。
    """
    flags = 0x01 if end_stream else 0x00
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def _encode_headers_frame(session_id: int, header_block: bytes, end_stream: bool = False) -> bytes:
    """HEADERS フレームのワイヤバイト列を組み立てる

    HPACK 圧縮済みヘッダーブロックを指定して HEADERS フレームを組み立てる。
    END_STREAM フラグ付きで受理と同時クローズの応答等を再現する。
    """
    flags = 0x04 | (0x01 if end_stream else 0x00)  # END_HEADERS | END_STREAM
    return (
        len(header_block).to_bytes(3, "big")
        + bytes([0x01, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + header_block
    )


def test_end_stream_only_closes_session() -> None:
    """END_STREAM のみでセッション終了が検知され send_datagram が送出されないことを確認

    ピアが WT_CLOSE_SESSION を送らず END_STREAM のみで CONNECT ストリームを
    閉じた場合 (draft-15 Section 3.4 の正規の終了経路) にセッション終了が
    検知され、SessionClosed (error_code 0、error_message 空) が発火する。
    WT_CLOSE_SESSION なしのクリーンクローズは error code 0 かつ空のエラー
    文字列の WT_CLOSE_SESSION と等価 (Section 6.12)。エントリ削除により
    send_datagram が塞がれる (エントリの削除は公開 API から直接観測でき
    ないため、送出抑止で間接検証する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (サーバー) が END_STREAM のみでストリームを閉じる
    ret = client.receive(_encode_data_frame(session_id, end_stream=True))
    assert ret > 0, "END_STREAM フレームの注入に失敗しました"

    # SessionClosed が error_code 0 で 1 回だけ発火する
    closed_events = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0
    assert closed_events[0].error_message == ""

    # END_STREAM 検知後の send_datagram はワイヤへ送出されない
    client.send_datagram(session_id, b"after-end-stream")
    wire = client.send()
    assert wire is None or _encode_capsule(0x00, b"after-end-stream") not in wire


def test_end_stream_after_recv_wt_close_session_no_double() -> None:
    """WT_CLOSE_SESSION + END_STREAM の両方を送るピアで SessionClosed が 1 回だけ発火することを確認

    コンプライアントなピアは WT_CLOSE_SESSION 送出後に必ず END_STREAM を送る
    (draft-15 Section 6.12 の MUST)。close_session は WT_CLOSE_SESSION と
    END_STREAM を同時送出するため、_h2_pump の時点で両方がサーバーに届く。
    カプセル処理 (handle_wt_close_session) が SessionClosed を発火してエントリ
    を削除するため、続く END_STREAM 検知 (handle_end_stream) とストリーム
    close (on_stream_close_callback) はエントリ不在で何もせず、SessionClosed
    は 1 回だけ発火する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_CLOSE_SESSION + END_STREAM を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)
    closed_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1


def test_end_stream_response_after_recv_wt_close_session() -> None:
    """WT_CLOSE_SESSION 受信後に受信者 MUST (END_STREAM で応答) に従い END_STREAM を送出することを確認

    draft-15 Section 6.12 の「受信者は WT_CLOSE_SESSION 受信時に END_STREAM
    フレームで応答してストリームを閉じる MUST」。エントリ削除とセットで行い、
    ストリームを両ハーフクローズで閉じて同時ストリーム枠を消費し続けない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # サーバーが空ペイロード + END_STREAM フラグの DATA フレームで応答する
    wire = server.send()
    assert wire is not None
    assert _encode_data_frame(session_id, end_stream=True) in wire


def test_initiator_session_closed_after_peer_end_stream_response() -> None:
    """close_session した側 (イニシエーター) もピアの END_STREAM 応答で SessionClosed が 1 回発火することを確認

    受信側 (受信者 MUST の END_STREAM 応答) と合わせて両ハーフが閉じると、
    イニシエーターの on_stream_close_callback が SessionClosed を発火する
    (error_code は nghttp2 のクローズ由来で 0)。セッション終了の通知が両側に
    1 回ずつ届く。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントが WT_CLOSE_SESSION を送り、サーバーが受信して END_STREAM で応答する
    client.close_session(session_id, 0)
    _h2_pump(client, server)
    _h2_pump(server, client)

    # イニシエーター側でも SessionClosed が 1 回発火する
    closed_events = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0
    assert closed_events[0].error_message == ""


def test_recv_wt_close_session_only_no_end_stream_response_and_closed() -> None:
    """END_STREAM を伴わない WT_CLOSE_SESSION のみでも END_STREAM 応答と SessionClosed 1 回が成立することを確認

    非コンプライアントなピア (WT_CLOSE_SESSION を送るが END_STREAM を送らない)
    でも、受信者 MUST の END_STREAM 応答は送出され、SessionClosed は 1 回だけ
    発火する (二重発火しない)。h2 の公開 API に WT_CLOSE_SESSION のみを送出
    する手段が存在しないため、ワイヤ注入で再現する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # WT_CLOSE_SESSION カプセルのみを DATA フレームで注入する (END_STREAM なし)
    # 0x6843 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint、0x04 は長さ
    wt_close_capsule = b"\x68\x43\x04" + (0).to_bytes(4, "big")
    ret = server.receive(_encode_data_frame(session_id, wt_close_capsule))
    assert ret > 0, "WT_CLOSE_SESSION カプセルの注入に失敗しました"

    # SessionClosed が 1 回発火する
    closed_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id

    # 受信者 MUST の END_STREAM 応答が送出される
    wire = server.send()
    assert wire is not None
    assert _encode_data_frame(session_id, end_stream=True) in wire


def test_queued_capsule_discarded_after_recv_wt_close_session() -> None:
    """WT_CLOSE_SESSION 受信時に終了前にキュー済みの未 flush カプセルが破棄されることを確認

    終了を学習する前にキュー済みの送出は送出され得る (既存の原則) が、受信
    経路では終了学習時に http2_stream_buffers_ が破棄されるため、flush 前の
    データグラムはワイヤに送出されない (エントリ削除とセットの設計)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # サーバーがデータグラムをキューする (flush 前)
    server.send_datagram(session_id, b"pre-close")

    # キューが flush される前にピアの WT_CLOSE_SESSION が届く
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信処理時にキュー済みのカプセルが破棄され、データグラムは送出されない
    wire = server.send()
    assert wire is None or _encode_capsule(0x00, b"pre-close") not in wire


def test_close_session_after_recv_wt_close_session_no_double() -> None:
    """WT_CLOSE_SESSION 受信後に close_session で応答しても SessionClosed が 1 回だけ発火することを確認

    受信側アプリが close_session で応答すると自側も END_STREAM を送出し、
    ピアの END_STREAM と合わせて両ハーフが閉じる。エントリを削除しない
    修正前実装では、このタイミングの on_stream_close_callback が SessionClosed
    を 2 回目に発火していた (1 回目は WT_CLOSE_SESSION 受信時の
    handle_wt_close_session)。エントリ削除により on_stream_close_callback が
    発火せず、1 回だけになる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信側で SessionClosed が 1 回発火している
    closed_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id

    # 受信側アプリが close_session で応答しても SessionClosed は追加発火しない
    server.close_session(session_id, 0)
    _h2_pump(server, client)
    closed_events = [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 0


def test_close_session_after_recv_wt_close_session_noop() -> None:
    """WT_CLOSE_SESSION 受信後の close_session が no-op (再送出なし) になることを確認

    エントリ削除が機能していることの間接検証。close_session はエントリ不在で
    WT_CLOSE_SESSION を再送出しない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信後の close_session は no-op (WT_CLOSE_SESSION の再送出なし)
    server.close_session(session_id, 0)
    wire = server.send()
    # 0x6843 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint
    assert wire is None or b"\x68\x43" not in wire


def test_send_stream_data_after_recv_wt_close_session_noop() -> None:
    """WT_CLOSE_SESSION 受信後の send_stream_data が no-op になることを確認

    ストリームが存在するセッションで WT_CLOSE_SESSION を受信するとエントリが
    削除されるため、以後の send_stream_data は get_wt_session の失敗でワイヤに
    送出されない (修正前はエントリが残るため送出され得た)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントがストリームを開きデータを送って、サーバーが受信する
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"data")
    _h2_pump(client, server)

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信後の send_stream_data は no-op (ワイヤに送出されない)
    server.send_stream_data(session_id, stream_id, b"after-close")
    wire = server.send()
    assert wire is None or b"after-close" not in wire


def test_end_stream_close_session_noop() -> None:
    """END_STREAM 検知後の close_session / send_stream_data が no-op になることを確認

    エントリ削除が機能していることの間接検証。close_session はエントリ不在
    で WT_CLOSE_SESSION を送出せず、send_stream_data も no-op になる
    (エントリの削除自体は公開 API から直接観測できない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (サーバー) が END_STREAM のみでストリームを閉じる
    ret = client.receive(_encode_data_frame(session_id, end_stream=True))
    assert ret > 0, "END_STREAM フレームの注入に失敗しました"

    # END_STREAM 検知後の close_session は no-op (WT_CLOSE_SESSION 送出なし)
    client.close_session(session_id, 0)
    wire = client.send()
    # 0x6843 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint
    assert wire is None or b"\x68\x43" not in wire

    # send_stream_data も no-op になる (ワイヤに送出されない)
    client.send_stream_data(session_id, 0, b"after-end-stream")
    wire = client.send()
    assert wire is None or b"after-end-stream" not in wire


def test_end_stream_201_no_termination() -> None:
    """201 応答のエントリでは END_STREAM で終了処理が実行されないことを確認

    201 は 2xx 非 200 のため is_established が false のまま残る (既知の
    制約。後始末経路が存在しない限り残留し続ける)。確立済みでないエントリ
    の END_STREAM は誤検知しない。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 201 で応答する (END_STREAM 付き HEADERS で届く)
    server.reject_session(session_id, 201)
    _h2_pump(server, client)

    # SessionClosed は発火しない (誤検知しない)
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(client))


def test_end_stream_wt_stream_fin_no_termination() -> None:
    """WT データストリームの FIN (WT_STREAM_FIN) がセッション終了として誤検知されないことを確認

    データストリームは wt_sessions_ ではなくセッションの streams に登録され
    る。WT_STREAM_FIN は HTTP/2 の END_STREAM フラグを伴わないため END_STREAM
    検知経路は実行されず、仮に END_STREAM が届いても get_wt_session が失敗
    する。カプセル FIN がセッション終了として扱われないことの仕様ピン。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントがデータストリームを開き、FIN 付きでデータを送る
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"data", fin=True)
    _h2_pump(client, server)

    # セッション終了として誤検知されない (SessionClosed は発火しない)
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(server))


def test_end_stream_normal_http_stream_no_termination() -> None:
    """エントリ不在の通常 HTTP/2 ストリームの END_STREAM で誤検知しないことを確認

    CONNECT でない通常の HTTP リクエスト (GET + END_STREAM) を受信しても
    wt_sessions_ にエントリが存在しないため、終了処理は実行されない。
    HPACK 動的テーブルを汚さないよう、注入はテスト内の最後の操作にする。
    """
    _client, server = _create_h2_session_pair()

    # 通常の HTTP リクエスト (GET + END_STREAM) を注入する
    header_block = b"\x82\x87\x84" + b"\x01\x09localhost"
    ret = server.receive(_encode_headers_frame(5, header_block, end_stream=True))
    assert ret > 0, "HEADERS フレームの注入に失敗しました"

    # セッション終了として誤検知されない (SessionClosed は発火しない)
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(server))


def test_headers_200_end_stream_ready_and_closed() -> None:
    """200 + END_STREAM (受理と同時クローズ) で SESSION_READY と SessionClosed が連続発火することを確認

    クライアントが 200 + END_STREAM を受信した場合、HCAT_RESPONSE 分岐で
    is_established = true が設定された後に END_STREAM 検知が走り、同一
    receive() 内で SESSION_READY と SessionClosed が連続発火する (正規の
    経路)。エントリ削除により以後の送信は塞がれる。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 200 + END_STREAM の HEADERS フレームを注入する
    header_block = b"\x88"  # indexed header field index 8 = :status: 200
    ret = client.receive(_encode_headers_frame(session_id, header_block, end_stream=True))
    assert ret > 0, "HEADERS フレームの注入に失敗しました"

    # SESSION_READY と SessionClosed が同一 receive() 内でこの順に連続発火する
    events = _drain_events(client)
    ready_events = [e for e in events if e.type == h2.EventType.SESSION_READY]
    closed_events = [e for e in events if e.type == h2.EventType.SESSION_CLOSED]
    assert len(ready_events) == 1
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0
    assert events.index(ready_events[0]) < events.index(closed_events[0])

    # エントリ削除により send_datagram は送出されない
    client.send_datagram(session_id, b"after-200-end")
    wire = client.send()
    assert wire is None or _encode_capsule(0x00, b"after-200-end") not in wire

    # 確立処理でキューされた初期フロー制御カプセル (WT_MAX_DATA /
    # WT_MAX_STREAMS) もセッション終了後に送出されない
    # 0x99 0x0b 0x4d 0x3d は WT_MAX_DATA (Type 0x190b4d3d) の 4 バイト varint
    assert wire is None or b"\x99\x0b\x4d\x3d" not in wire


def test_end_stream_server_pre_accept_fin_no_termination() -> None:
    """サーバー側の受理前 FIN (CONNECT + END_STREAM) で終了処理が実行されないことを確認

    CONNECT リクエストの HEADERS に END_STREAM が付く受理前 FIN は、確立
    済みでないため検知対象外となり、エントリが残留する (h3 側の受理前 FIN
    対応の h2 版は本対応のスコープ外)。HPACK 動的テーブルを汚さないよう、
    注入はテスト内の最後の操作にする。
    """
    _client, server = _create_h2_session_pair()

    # CONNECT + END_STREAM の HEADERS フレームを注入する
    header_block = (
        b"\x00\x07:method\x07CONNECT"
        + b"\x00\x09:protocol\x0cwebtransport"
        + b"\x87"  # :scheme: https
        + b"\x84"  # :path: /
        + b"\x01\x09localhost"  # :authority: localhost
    )
    ret = server.receive(_encode_headers_frame(1, header_block, end_stream=True))
    assert ret > 0, "HEADERS フレームの注入に失敗しました"

    # セッション終了として誤検知されない (SessionClosed は発火しない)
    assert all(e.type != h2.EventType.SESSION_CLOSED for e in _drain_events(server))
