"""WebTransport over HTTP/2 の受信ヘッダーバッファのテスト

`H2Session::pending_headers_` は受信途中のヘッダーブロックを保持する。
nghttp2-v2 はヘッダーブロックの種別をコールバックで分離しており、
リクエスト / レスポンス / 1xx 中間応答は `begin_headers` / `recv_header` /
`end_headers`、trailer は `begin_trailers` / `recv_trailer` / `end_trailers`
で通知する (nghttp2.h の nghttp2_callbacks の説明)。

v2 では完了したヘッダーブロックは `end_headers` / `end_trailers` で
解放するため、v1 で問題になった「trailer と 1xx 後の最終応答のエントリが
残る」現象は起きない。一方、次の挙動は v1 から変わっている。

- CONNECT リクエストへの trailer は nghttp2-v2 が PROTOCOL_ERROR として
  接続を終了する (GOAWAY を予約する)。trailer は受信バッファへ蓄積されない
- 不正なヘッダー名 (大文字) は nghttp2-v2 が接続エラーとして扱い、GOAWAY を
  予約する (v1 はストリーム単位で閉じていた)。受信バッファのエントリは
  接続オブジェクトの破棄まで残る
- 1xx 中間応答の後の最終応答も nghttp2-v2 は通知するため、セッションの
  確立・拒否として正しく処理できる (v1 では捕捉できなかった)
"""

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


def test_trailer_on_connect_stream_is_rejected() -> None:
    """CONNECT ストリームへの trailer が nghttp2-v2 により接続エラーになることを確認

    nghttp2-v2 の HTTP 状態機械は CONNECT リクエストへの trailer を
    NGHTTP2_ERR_PROTO として扱い、接続を終了する (GOAWAY を予約する)。
    trailer は受信バッファへ蓄積されない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    first = _encode_header_block(b"x-trailer", b"end")
    frame = _encode_headers_frame(
        session_id, first + _encode_header_block(b"x-second", b"1"), end_stream=True
    )

    ret = server.receive(frame)
    assert ret > 0, "trailer の注入に失敗しました"

    # trailer は受信バッファへ蓄積されない
    assert server._test_pending_header_count(session_id) is None, (
        "CONNECT ストリームへの trailer が受信バッファへ蓄積されている"
    )
    # 接続エラーとして GOAWAY が予約される
    assert server.want_write() is True, "trailer の接続エラーで GOAWAY が予約されていない"


def test_final_response_after_1xx_establishes_session() -> None:
    """1xx 中間応答の後の最終応答がセッション確立として扱われることを確認

    nghttp2-v2 は 1xx の後でも最終応答のヘッダーブロックを end_headers で
    通知するため、v1 では捕捉できなかった「1xx 後の最終応答」も 2xx の判定に
    使える。受信バッファはどちらの応答の完了時にも解放される。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 1xx (103 Early Hints) → 最終応答 (200) の順に注入する
    ret = client.receive(_encode_headers_frame(session_id, _encode_status_header_block(103)))
    assert ret > 0, "1xx の注入に失敗しました"
    assert client.get_session_ids() == [], "1xx でセッションが確立している"
    assert client._test_pending_header_count(session_id) is None, (
        "1xx の受信完了後に受信ヘッダーバッファのエントリが残っている"
    )

    ret = client.receive(_encode_headers_frame(session_id, _encode_status_header_block(200)))
    assert ret > 0, "最終応答の注入に失敗しました"
    assert not client.is_closed(), "応答の注入で接続が閉じられました"
    # 最終応答 (200) でセッションが確立する
    assert client.get_session_ids() == [session_id], "1xx 後の最終応答でセッションが確立していない"
    assert client._test_pending_header_count(session_id) is None, (
        "最終応答の受信完了後に受信ヘッダーバッファのエントリが残っている"
    )


def test_malformed_header_name_terminates_connection() -> None:
    """不正なヘッダー名が nghttp2-v2 により接続エラーになることを確認

    nghttp2-v2 は大文字を含むヘッダー名 (RFC 9113 Section 8.2.1 の MUST 違反)
    を接続エラーとして扱い、GOAWAY を予約する (v1 はストリーム単位で閉じて
    いた)。受信途中のエントリは接続オブジェクトの破棄まで保持される。
    """
    # クライアントは SETTINGS 交換のために必要で、HEADERS はワイヤ注入で送る
    _client, server = _create_h2_session_pair()

    # 検証を通るヘッダー (疑似ヘッダー 4 件と x-ok) と、検証で拒否される
    # 大文字を含むヘッダー名を続けて並べる
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

    # 拒否されるヘッダーを渡すと nghttp2-v2 が接続エラーとして GOAWAY を予約する。
    # 受信途中のエントリは接続オブジェクトの破棄まで保持される
    ret = server.receive(frame[9 + len(valid_block) :])
    assert ret > 0, "不正なヘッダーの注入に失敗しました"
    assert server.want_write() is True, "不正なヘッダーの接続エラーで GOAWAY が予約されていない"
