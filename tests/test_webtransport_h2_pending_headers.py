"""WebTransport over HTTP/2 の受信ヘッダーバッファの解放テスト

`H2Session::pending_headers_` は受信途中のヘッダーブロックを保持する。
nghttp2 は完了したヘッダーブロックを `NGHTTP2_HCAT_REQUEST` /
`NGHTTP2_HCAT_RESPONSE` / `NGHTTP2_HCAT_PUSH_RESPONSE` /
`NGHTTP2_HCAT_HEADERS` の 4 種別で通知する (nghttp2.h の
nghttp2_headers_category の docstring)。本実装が削除していたのは前 2 種のみで、
trailer と 1xx 中間応答の後に届く最終応答 (`NGHTTP2_HCAT_HEADERS`) の
エントリが接続終了まで残っていた。

nghttp2 の分類は次のとおりである (一次資料と実測)。

- サーバー側の trailer は `NGHTTP2_HCAT_HEADERS` (END_STREAM の有無を
  問わず到達する)
- 1xx 中間応答は `NGHTTP2_HCAT_RESPONSE`、その後はストリームが OPENED に
  なるため最終応答は `NGHTTP2_HCAT_HEADERS` (応答として検証される)
- ヘッダー単位の拒否 (不正なヘッダー名など) では `nghttp2_http_on_header` の
  検証が `on_header_callback` より先に走って拒否され、nghttp2 は
  `on_frame_recv_callback` を発火せずにストリームを閉じる (実測)

解放は `H2Session::on_frame_recv_callback` の `NGHTTP2_HCAT_HEADERS` の
完了経路と、`H2Session::on_stream_close_callback` で行う。受信途中のまま
放置して `RST_STREAM` を注入した場合は nghttp2 がストリームを閉じないため
`on_stream_close_callback` は発火しない (実測) が、上記の拒否経路では発火する
ため、ストリーム終了経路もテストで検証する。

`NGHTTP2_HCAT_PUSH_RESPONSE` と PUSH_PROMISE の経路は本対応の対象外であり、
完了したブロックのエントリはストリーム終了時まで残る (別の対応)。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _encode_headers_frame,
    _encode_status_header_block,
    _h2_pump,
)


def _encode_header_block(name: bytes, value: bytes) -> bytes:
    """ヘッダー 1 件の HPACK ヘッダーブロックを組み立てる

    RFC 7541 Section 6.2.2 の without Indexing のリテラル表現 (0x00 プレフィックス)
    で組み立てるため、動的テーブルを汚さない。
    """
    return bytes([0x00, len(name)]) + name + bytes([len(value)]) + value


def test_trailer_releases_pending_header_count() -> None:
    """trailer の受信完了で受信ヘッダーバッファが解放されることを確認

    trailer は NGHTTP2_HCAT_HEADERS で通知されるため、HCAT_REQUEST /
    HCAT_RESPONSE の分岐では削除されない。完了したブロックはストリームが
    閉じなくても解放される。受信途中ではデコード済みのヘッダーが保持される
    ため、2 件目のヘッダーを渡す前の時点でエントリの中身を固定する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    first = _encode_header_block(b"x-trailer", b"end")
    frame = _encode_headers_frame(
        session_id, first + _encode_header_block(b"x-second", b"1"), end_stream=True
    )
    # 1 件目のヘッダーまでを渡すと、デコード済みの 1 件が保持された状態になる
    ret = server.receive(frame[: 9 + len(first)])
    assert ret > 0, "trailer の一部の注入に失敗しました"
    assert server._test_pending_header_count(session_id) == 1, (
        "受信途中のヘッダーブロックのエントリが保持されていない"
    )

    # 残りを渡すとブロックが完了し、エントリが解放される
    ret = server.receive(frame[9 + len(first) :])
    assert ret > 0, "trailer の残りの注入に失敗しました"
    assert not server.is_closed(), "trailer の注入で接続が閉じられました"

    assert server._test_pending_header_count(session_id) is None, (
        "trailer の受信完了後に受信ヘッダーバッファのエントリが残っている"
    )


def test_final_response_after_1xx_releases_pending_header_count() -> None:
    """1xx 中間応答の後に届く最終応答の完了で受信ヘッダーバッファが解放されることを確認

    1xx はストリームが OPENING の間に届くため NGHTTP2_HCAT_RESPONSE として
    通知され、エントリはその分岐の無条件削除で消える。その後の最終応答は
    ストリームが OPENED になった後なので NGHTTP2_HCAT_HEADERS として通知
    され、NGHTTP2_HCAT_HEADERS の分岐が無い実装では削除されない。wt_sessions_
    の残留 (1xx 後の最終応答の捕捉不能) は本対応の対象外であり、ここでは
    受信ヘッダーバッファだけを検証する。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 1xx (103 Early Hints) → 最終応答 (200) の順に注入する
    ret = client.receive(_encode_headers_frame(session_id, _encode_status_header_block(103)))
    assert ret > 0, "1xx の注入に失敗しました"
    ret = client.receive(_encode_headers_frame(session_id, _encode_status_header_block(200)))
    assert ret > 0, "最終応答の注入に失敗しました"
    assert not client.is_closed(), "応答の注入で接続が閉じられました"
    # 1xx は中間応答であり、セッションは確立しない
    assert client.get_session_ids() == [], "1xx と最終応答でセッションが確立している"

    assert client._test_pending_header_count(session_id) is None, (
        "1xx 後の最終応答の受信完了後に受信ヘッダーバッファのエントリが残っている"
    )


def test_stream_close_releases_pending_header_count() -> None:
    """ストリーム終了で受信ヘッダーバッファが解放されることを確認

    不正なヘッダー名 (大文字) を含むヘッダーブロックでは、nghttp2 は
    `nghttp2_http_on_header` の検証で当該ヘッダーを拒否し、
    `on_frame_recv_callback` を呼ばずにストリームを閉じる。完了通知が届かない
    ため、解放は `on_stream_close_callback` だけが担う。ここでは検証を通る
    ヘッダーが実際に保持された状態を作ってから拒否を起こし、保持中のエントリが
    ストリーム終了で解放されることを検証する (保持が空のままでも解放は観測
    できるが、それでは本経路の解放対象を固定したことにならない)。
    """
    # クライアントは SETTINGS 交換のために必要で、HEADERS はワイヤ注入で送る
    _client, server = _create_h2_session_pair()

    # 検証を通るヘッダー (疑似ヘッダー 4 件と x-ok) と、検証で拒否される
    # 大文字を含むヘッダー名 (RFC 9113 Section 8.2.1 の MUST 違反) を続けて並べる
    valid_block = (
        _encode_header_block(b":method", b"GET")
        + _encode_header_block(b":scheme", b"https")
        + _encode_header_block(b":authority", b"localhost")
        + _encode_header_block(b":path", b"/")
        + _encode_header_block(b"x-ok", b"1")
    )
    frame = _encode_headers_frame(1, valid_block + _encode_header_block(b"X-Bad", b"1"))

    # 拒否されるヘッダーを渡す前まで注入すると、5 件が保持された状態になる
    ret = server.receive(frame[: 9 + len(valid_block)])
    assert ret > 0, "正常なヘッダーの注入に失敗しました"
    assert server._test_pending_header_count(1) == 5, (
        "受信途中のヘッダーブロックのエントリが保持されていない"
    )

    # 拒否されるヘッダーを渡すと nghttp2 がストリームを閉じる
    ret = server.receive(frame[9 + len(valid_block) :])
    assert ret > 0, "不正なヘッダーの注入に失敗しました"
    assert not server.is_closed(), "不正なヘッダーの注入で接続が閉じられました"

    assert server._test_pending_header_count(1) is None, (
        "ストリーム終了後に受信ヘッダーバッファのエントリが残っている"
    )
