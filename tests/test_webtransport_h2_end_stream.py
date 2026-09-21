"""WebTransport over HTTP/2 の END_STREAM によるセッション終了検知テスト

ピアが WT_CLOSE_SESSION カプセルを送らず END_STREAM フレームのみで CONNECT
ストリームを閉じた場合 (draft-ietf-webtrans-http2-15 Section 3.4 の正規の
終了経路) にセッション終了を検知する修正を検証する。WT_CLOSE_SESSION なし
のクリーンクローズは error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION
と等価 (Section 6.12)。

あわせて、WT_CLOSE_SESSION 受信後の挙動 (Section 6.12 の受信者 MUST である
END_STREAM 応答の送出と、受信後の close_session / send_stream_data が
塞がれて SessionClosed が二重発火しないこと) を検証する。

さらに、非 WebTransport リクエストへの自動 405 応答 (Allow: CONNECT と
END_STREAM 付き) も検証する。

RFC 9297 Section 3.3 の第 3 段落は、カプセルを運ぶストリームの受信側が
クリーンに終了し (HTTP/2 では END_STREAM)、そのとき最後のカプセルがまだ途中
だった場合を malformed / incomplete なメッセージとして扱う MUST を定める。
WT_CLOSE_SESSION を伴わず END_STREAM のみで終わった場合、カプセル境界で
終わったときだけがクリーンな終了 (error code 0) であり、途中で終わった場合は
PROTOCOL_ERROR のストリームエラー (RFC 9113 Section 8.1.1) になることも
検証する。
"""

from __future__ import annotations

import pytest
from conftest import (
    _PROTOCOL_ERROR,
    _assert_session_closed_by_protocol_error,
    _connect_h2_session,
    _create_h2_http2_pair,
    _create_h2_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_rst_stream_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2, http2

# WT_MAX_DATA の Type (4 バイト可変長整数)。切り詰めの 5 通り (Type varint
# 途中 / Type のみ / Length varint 途中 / Length のみ / ペイロード途中) を組み立てる
_WT_MAX_DATA_TYPE_VARINT: bytes = _encode_varint(0x190B4D3D)
_TRUNCATED_CAPSULES: list[bytes] = [
    _WT_MAX_DATA_TYPE_VARINT[:2],
    _WT_MAX_DATA_TYPE_VARINT,
    _WT_MAX_DATA_TYPE_VARINT + b"\x40",
    _WT_MAX_DATA_TYPE_VARINT + b"\x08",
    _WT_MAX_DATA_TYPE_VARINT + b"\x08" + b"\x00\x00\x00\x00",
]


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


@pytest.mark.parametrize(
    "truncated",
    _TRUNCATED_CAPSULES,
    ids=["type_partial", "type_only", "length_partial", "length_only", "payload_partial"],
)
def test_end_stream_with_truncated_capsule_resets_stream(truncated: bytes) -> None:
    """カプセルの切り詰めのまま END_STREAM を受信すると PROTOCOL_ERROR の RST_STREAM になることを確認

    根拠はモジュール docstring。修正前は未処理のバッファを検査せず、error code 0 の
    正常終了としてセッションを閉じていた。切り詰めだけではセッションは終了せず
    (後続の DATA を待つ)、END_STREAM が届いた時点でリセットされることも表明する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # カプセルが途中の DATA (END_STREAM なし) を届ける
    ret = server.receive(_encode_data_frame(session_id, truncated))
    assert ret > 0, "カプセルの注入に失敗しました"
    assert server._test_unfinished_capsule_bytes(session_id) == len(truncated), (
        "切り詰めが未完成カプセルとして保持されていません"
    )
    assert server.get_session_ids() == [session_id], (
        "カプセルが途中の時点でセッションが終了しています"
    )

    # 空の DATA + END_STREAM でピアがストリームを閉じる (draft-15 Section 3.4)
    ret = server.receive(_encode_data_frame(session_id, b"", end_stream=True))
    assert ret > 0, "END_STREAM の注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id)


def test_end_stream_in_same_frame_as_truncated_capsule_resets_stream() -> None:
    """切り詰めと END_STREAM が同一 DATA フレームでも PROTOCOL_ERROR になることを確認

    nghttp2 は DATA のペイロード通知 (カプセル処理) をフレーム通知 (END_STREAM
    検知) より先に行うため、フレームを分けた場合と同じ観測になる。1 回の
    receive() に切り詰めと END_STREAM が同居する入力でも、カプセル処理で
    バッファに残った分が END_STREAM 検知時に検証されることを固定する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _WT_MAX_DATA_TYPE_VARINT, end_stream=True))
    assert ret > 0, "切り詰めと END_STREAM の注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id)


def test_end_stream_after_complete_capsule_closes_cleanly() -> None:
    """完成したカプセルの直後の END_STREAM は従来どおり error code 0 で終了することを確認

    カプセル境界で終わった場合は RFC 9297 Section 3.3 の切り詰めに該当せず、
    draft-15 Section 6.12 の「WT_CLOSE_SESSION 無しのクリーンな終了」になる
    (対照)。受信側のカプセルバッファが空のときに END_STREAM が届いても
    RST_STREAM を送出しないことも表明する (wire の表明は補助であり、主たる
    判別は SessionClosed の error_code 0)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 完成したカプセル (PADDING。draft-15 Section 6.1) を届けてバッファを空にする
    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(0x190B4D38, b"")))
    assert ret > 0, "カプセルの注入に失敗しました"
    assert server._test_unfinished_capsule_bytes(session_id) == 0, (
        "完成したカプセルでバッファが空になっていません"
    )

    ret = server.receive(_encode_data_frame(session_id, b"", end_stream=True))
    assert ret > 0, "END_STREAM の注入に失敗しました"

    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1, "SessionClosed が 1 件だけ発火していません"
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0, "クリーンな終了の error code が 0 ではありません"
    assert closed_events[0].error_message == ""
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    wire = server.send()
    assert wire is None or _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "カプセル境界の END_STREAM で RST_STREAM が送出されました"
    )


def test_end_stream_pre_accept_truncated_capsule_no_termination() -> None:
    """受理前のセッションでは切り詰められたカプセルを検証しないことを確認

    サーバーは受理するまでカプセルを処理せず、受理前の DATA は上限付きで蓄積
    される (draft-15 Section 3.2 の楽観送信。RFC 9297 Section 3.2 の Capsule
    Protocol も 2xx まで not in use)。確立前は `handle_end_stream` の確立判定で
    早期 return するため切り詰めの検証も行われず、エントリと蓄積が残る。
    検査を早期 return より前に置く誤実装を検出する。

    背景 (対象外): 受理前にピアが END_STREAM だけで閉じた場合の終了通知 (受理前
    FIN の検知) は本対応の対象外である。この場合、後で accept_session しても
    終了通知も切り詰めの検証も行われず、エントリと蓄積が残る。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0, "CONNECT リクエストの送信に失敗しました"
    _h2_pump(client, server)
    ready_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1, "SESSION_READY が 1 件だけ発火していません"

    # 受理前にカプセルが途中の DATA を届ける (上限付きで蓄積される)
    ret = server.receive(_encode_data_frame(session_id, _WT_MAX_DATA_TYPE_VARINT))
    assert ret > 0, "切り詰めカプセルの注入に失敗しました"

    # 受理前に空の DATA + END_STREAM でピアがストリームを閉じる
    ret = server.receive(_encode_data_frame(session_id, b"", end_stream=True))
    assert ret > 0, "END_STREAM の注入に失敗しました"

    assert server._test_unfinished_capsule_bytes(session_id) == len(_WT_MAX_DATA_TYPE_VARINT), (
        "受理前の END_STREAM 後に切り詰めが保持されていません"
    )
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in _drain_events(server)), (
        "受理前のセッションで SessionClosed が発火しました"
    )
    wire = server.send()
    assert wire is None or _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "受理前のセッションで RST_STREAM が送出されました"
    )
    assert server.get_session_ids() == [], "受理前のセッションが確立扱いになっています"


def test_end_stream_after_accept_with_truncated_pre_accept_buffer_resets_stream() -> None:
    """受理前バッファに残った切り詰めが受理後の END_STREAM で検証されることを確認

    受理前の DATA は蓄積され、accept_session の排出では切り詰めの分が
    バッファに残る (カプセルのフレーミングが揃うまで後続のバイトを待つ)。受理後に
    END_STREAM が届いた時点で切り詰めとして検証され、PROTOCOL_ERROR の
    RST_STREAM になる。受理時に残バッファをリセットする実装 (後続を待たずに
    誤って malformed にする) を検出する。
    """
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0, "CONNECT リクエストの送信に失敗しました"
    _h2_pump(client, server)

    # 受理前にカプセルが途中の DATA を届ける (蓄積される)
    ret = server.receive(_encode_data_frame(session_id, _WT_MAX_DATA_TYPE_VARINT))
    assert ret > 0, "切り詰めカプセルの注入に失敗しました"
    assert server.get_session_ids() == [], "受理前にセッションが確立しています"

    # 受理すると蓄積分が排出されるが、切り詰めは後続を待って残る
    assert server.accept_session(session_id) is True, "セッションの受理に失敗しました"
    assert server.get_session_ids() == [session_id], "セッションが確立していません"
    assert server._test_unfinished_capsule_bytes(session_id) == len(_WT_MAX_DATA_TYPE_VARINT), (
        "受理後も切り詰めが未完成カプセルとして残っていません"
    )
    wire = server.send()
    assert wire is not None, "受理時の応答が送出されていません"
    assert _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "受理時に切り詰めをリセットしています"
    )
    client.receive(wire)

    # 受理後の END_STREAM で切り詰めが検証される
    ret = server.receive(_encode_data_frame(session_id, b"", end_stream=True))
    assert ret > 0, "END_STREAM の注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id)
    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "RST_STREAM によるセッション終了後に未消費受信バイトの記録が残っている"
    )


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


def test_end_stream_201_terminates_session() -> None:
    """201 応答のセッションは END_STREAM で終了処理が実行されることを確認

    201 は 2xx 全般のセッション確立 (draft-15 Section 3.2) であり、
    is_established = true となる。確立済みセッションの END_STREAM は
    セッション終了として検知され、SessionClosed が発火する。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 201 で応答する (END_STREAM 付き HEADERS で届く)
    server.reject_session(session_id, 201)
    _h2_pump(server, client)

    # 確立済みセッションの END_STREAM は SessionClosed を 1 回だけ発火させる
    closed_events = [
        event for event in _drain_events(client) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0
    assert client.get_session_ids() == []


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


@pytest.mark.parametrize(
    "request_headers",
    [
        [
            (":method", "GET"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ],
        [
            (":method", "CONNECT"),
            (":authority", "localhost"),
        ],
        [
            (":method", "CONNECT"),
            (":protocol", "websocket"),
            (":scheme", "https"),
            (":authority", "localhost"),
            (":path", "/"),
        ],
    ],
    ids=["get", "connect_without_protocol", "connect_other_protocol"],
)
def test_non_webtransport_request_returns_405_with_allow(
    request_headers: list[tuple[str, str]],
) -> None:
    """非 WebTransport リクエストに 405 と Allow: CONNECT が END_STREAM 付きで返ることを確認

    WebTransport 専用エンドポイントとして、CONNECT + :protocol=webtransport
    以外のリクエストはストリームを滞留させず 405 で拒否する。WT 判定が
    `CONNECT かつ webtransport` の論理積であることを固定するため、GET・
    :protocol なしの CONNECT・:protocol が webtransport 以外の CONNECT を
    ケースに含める。RFC 9110 Section 15.5.6 の MUST に従い Allow: CONNECT
    を伴い、応答の END_STREAM でストリームを終端する。応答ヘッダーを観測
    するクライアントには http2.Connection (Sans-IO) を使う (h2.Session
    クライアントは平文リクエストを送出できない)。
    """
    client, server = _create_h2_http2_pair()

    # 非 WT リクエストを送る (submit_request は END_STREAM を付けないため
    # send_data(..., eof=True) でリクエストを終端する)
    stream_id = client.submit_request(request_headers)
    assert stream_id > 0
    client.send_data(stream_id, b"", eof=True)
    _h2_pump(client, server)

    # サーバーが自動で 405 応答を返す
    _h2_pump(server, client)

    # HEADERS イベントで :status 405 と allow: CONNECT を確認する
    events = _drain_events(client)
    headers_events = [event for event in events if event.type == http2.EventType.HEADERS]
    assert len(headers_events) == 1
    assert headers_events[0].stream_id == stream_id
    response_headers = dict(headers_events[0].headers)
    assert response_headers[":status"] == "405"
    assert response_headers["allow"] == "CONNECT"

    # 405 応答の END_STREAM でストリームが終端される
    assert any(
        event.type == http2.EventType.STREAM_END and event.stream_id == stream_id
        for event in events
    )


def test_non_webtransport_request_without_end_stream_returns_405() -> None:
    """終端前の非 WebTransport リクエストにも 405 が END_STREAM 付きで返ることを確認

    405 はリクエストの END_STREAM を待たず、HEADERS 受信時点で送出する。
    ボディ送信途中 (eof なし) の POST でも応答が返り、ストリームが応答待ち
    のまま滞留しないことを表明する。
    """
    client, server = _create_h2_http2_pair()

    # POST のヘッダーとボディの一部だけを送る (END_STREAM は送らない)
    stream_id = client.submit_request(
        [
            (":method", "POST"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    client.send_data(stream_id, b"partial-body", eof=False)
    _h2_pump(client, server)

    # リクエストの終端を待たずに 405 が返る
    _h2_pump(server, client)

    events = _drain_events(client)
    headers_events = [event for event in events if event.type == http2.EventType.HEADERS]
    assert len(headers_events) == 1
    assert headers_events[0].stream_id == stream_id
    response_headers = dict(headers_events[0].headers)
    assert response_headers[":status"] == "405"
    assert response_headers["allow"] == "CONNECT"
    assert any(
        event.type == http2.EventType.STREAM_END and event.stream_id == stream_id
        for event in events
    )


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
