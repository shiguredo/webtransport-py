"""WebTransport over HTTP/2 クライアントの非 2xx 応答受信時のセッション ID 削除テスト

サーバーが reject_session (非 2xx 応答) で CONNECT リクエストを拒否しても、
クライアントの wt_sessions_ にエントリが残り続け、拒否されたセッション ID
宛の send_datagram が DATAGRAM capsule をワイヤへ送出し続ける問題の修正を
検証する。draft-ietf-webtrans-http2-15 Section 3.2 の「A WebTransport
session is established when the server sends a 2xx response」により、非 2xx
で拒否されたセッションは一度も確立されておらず、終了通知 (SessionClosed)
の意味論が合わないため黙って削除する。
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


def _encode_1xx_headers(session_id: int, status_code: int) -> bytes:
    """1xx レスポンスの HEADERS フレームのワイヤバイト列を組み立てる

    HPACK 圧縮済みヘッダーブロックは、静的テーブルの :status (index 8) を
    参照するリテラルヘッダーフィールド (RFC 7541 Section 6.2.2 の
    Literal Header Field without Indexing) で組み立てる。0x08 は 4 ビット
    プレフィックスで index 8 を表し、インクリメンタルインデックスを伴わ
    ないためデコーダーの動的テーブルを汚さない。フレームは END_HEADERS
    フラグ付きの HEADERS フレーム (1xx は中間応答のため END_STREAM なし)。
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
    合わない。黙って削除する。本テストは修正前実装でも通る設計ピンであり、
    SessionClosed 不発火の意味論を守る。
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


def test_client_response_201_session_kept() -> None:
    """2xx 非 200 応答 (201) ではエントリが削除されず送出が続くことを確認

    draft-15 Section 3.2 では 2xx 全般がセッション確立であり、削除条件は
    「200 以外」ではなく「2xx 以外」。201 のエントリは is_established が
    false のまま残る既存の制約が続く (リーク挙動のピン留め)。本テストは
    修正前実装でも通る設計ピンであり、過剰削除の防止を守る。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 201 で応答する (reject_session は任意の status_code で
    # 応答を生成できる)
    server.reject_session(session_id, 201)
    _h2_pump(server, client)

    # 201 では削除されない: DATAGRAM capsule がワイヤに送出され続ける
    client.send_datagram(session_id, b"alive")
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x00, b"alive") in wire


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
    ret = client.receive(_encode_1xx_headers(session_id, 103))
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
    ret = client.receive(_encode_1xx_headers(session_id, 103))
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

    SESSION_READY の発火条件 (200 のみ) は変更しない。本テストは修正前
    実装でも通る回帰ピンであり、通常の確立経路の維持を守る。
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
