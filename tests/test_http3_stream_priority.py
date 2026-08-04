"""HTTP/3 の優先度制御 API テスト"""

from __future__ import annotations

from webtransport import http3


def _pump(src: http3.Connection, dst: http3.Connection) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)。get_streams_to_send は
    1 回の呼び出しで全てのデータを返すとは限らないため、データが無く
    なるまで繰り返す
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def _create_connection_pair() -> tuple[http3.Connection, http3.Connection]:
    """Http3Connection のクライアント・サーバーペアを作成して初期化する

    @return (クライアント Connection, サーバー Connection)
    """
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # クライアントからの双方向ストリームを受け入れる準備をする
    # (設定しないと PRIORITY_UPDATE フレームが H3_ID_ERROR で拒否される)
    server.set_max_client_streams_bidi(100)

    # 双方の SETTINGS を交換する
    _pump(server, client)
    _pump(client, server)

    return client, server


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http3_stream_priority_default() -> None:
    """Priority ヘッダーなしのリクエストはデフォルト優先度を返すことを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    assert server.stream_priority(0) == (3, False)


def test_http3_stream_priority_with_priority_header() -> None:
    """Priority ヘッダーの値がストリーム優先度に反映されることを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers() + [("priority", "u=2, i")]) is True
    _pump(client, server)
    assert server.next_event() is not None

    assert server.stream_priority(0) == (2, True)


def test_http3_stream_priority_client_guard() -> None:
    """クライアントの stream_priority が None を返すことを確認

    nghttp3 は conn->server を assert するため、C++ 側のガードで
    クライアントの呼び出しを拒否する
    """
    client, _server = _create_connection_pair()
    assert client.stream_priority(0) is None


def test_http3_stream_priority_not_found() -> None:
    """存在しないストリームの stream_priority が None を返すことを確認

    クライアント起動双方向ストリーム (%4 == 0) だが未作成の stream 4 を
    使う (STREAM_NOT_FOUND 経路)
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)

    assert server.stream_priority(4) is None


def test_http3_stream_priority_invalid_stream_id() -> None:
    """クライアント起動双方向でない stream_id の stream_priority が None を返す

    サーバー起動双方向ストリーム (%4 == 1) は優先度の対象外であり、
    nghttp3 が INVALID_ARGUMENT を返す
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)

    assert server.stream_priority(1) is None


def test_http3_stream_priority_out_of_range() -> None:
    """範囲外の stream_id で stream_priority がクラッシュしないことを確認

    nghttp3 は assert で stream_id の範囲を検証するため、C++ 側のガードで
    範囲外の値には None を返す
    """
    client, server = _create_connection_pair()
    assert server.stream_priority(-1) is None
    # NGHTTP3_MAX_VARINT (2**62 - 1) を超える値もガードされる
    assert server.stream_priority(1 << 62) is None
    assert client.stream_priority(-1) is None


def test_http3_client_stream_priority() -> None:
    """クライアントの優先度設定が PRIORITY_UPDATE でサーバーに届くことを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    assert client.client_stream_priority(0, 2, True) is True
    _pump(client, server)

    # クライアントのシリアライズ (u=2, i) がサーバーでパースされて反映される
    assert server.stream_priority(0) == (2, True)


def test_http3_client_stream_priority_server_guard() -> None:
    """サーバーの client_stream_priority が False を返すことを確認

    nghttp3 は !conn->server を assert するため、C++ 側のガードで
    サーバーの呼び出しを拒否する
    """
    _client, server = _create_connection_pair()
    assert server.client_stream_priority(0, 2, True) is False


def test_http3_client_stream_priority_invalid_urgency() -> None:
    """範囲外の urgency で client_stream_priority が False を返すことを確認

    urgency は 0-7 のみ。範囲外の値はピアが PRIORITY_UPDATE を
    パースできずコネクションエラーになるため、C++ 側で拒否する
    """
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    assert client.client_stream_priority(0, 8, True) is False


def test_http3_client_stream_priority_invalid_stream_id() -> None:
    """範囲外の stream_id で client_stream_priority が False を返すことを確認"""
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    assert client.client_stream_priority(-1, 2, True) is False


def test_http3_client_stream_priority_control_stream_unbound() -> None:
    """制御ストリーム未バインドの client_stream_priority が False を返す

    nghttp3 は tx.ctrl のフレームキューに PRIORITY_UPDATE を積むため、
    C++ 側のガードで未バインドの呼び出しを拒否する
    """
    client = http3.Connection.create_client(http3.Config())
    assert client.client_stream_priority(0, 2, True) is False


def test_http3_client_stream_priority_not_found() -> None:
    """存在しないストリームの client_stream_priority が False を返すことを確認

    クライアント起動双方向ストリーム (%4 == 0) だが未作成の stream 4 を
    使う (STREAM_NOT_FOUND 経路)
    """
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    assert client.client_stream_priority(4, 2, True) is False


def test_http3_client_stream_priority_not_bidi() -> None:
    """クライアント起動双方向でない stream_id の client_stream_priority が False

    サーバー起動双方向ストリーム (%4 == 1) は優先度の対象外であり、
    nghttp3 が INVALID_ARGUMENT を返す
    """
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    assert client.client_stream_priority(1, 2, True) is False


def test_http3_server_stream_priority() -> None:
    """サーバーの優先度設定が stream_priority で読めることを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    assert server.server_stream_priority(0, 5, False) is True
    assert server.stream_priority(0) == (5, False)


def test_http3_server_stream_priority_overrides_client() -> None:
    """サーバーの優先度設定後にクライアントの更新が無視されることを確認

    nghttp3 は SERVER_PRIORITY_SET フラグが立っているストリームへの
    PRIORITY_UPDATE を無視する
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    assert server.server_stream_priority(0, 5, False) is True
    assert client.client_stream_priority(0, 2, True) is True
    _pump(client, server)

    # サーバーが設定した優先度が維持される
    assert server.stream_priority(0) == (5, False)


def test_http3_server_stream_priority_client_guard() -> None:
    """クライアントの server_stream_priority が False を返すことを確認

    nghttp3 は conn->server を assert するため、C++ 側のガードで
    クライアントの呼び出しを拒否する
    """
    client, _server = _create_connection_pair()
    assert client.server_stream_priority(0, 5, False) is False


def test_http3_server_stream_priority_invalid_urgency() -> None:
    """範囲外の urgency で server_stream_priority が False を返すことを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)

    assert server.server_stream_priority(0, 8, True) is False


def test_http3_server_stream_priority_not_found() -> None:
    """存在しないストリームの server_stream_priority が False を返すことを確認

    クライアント起動双方向ストリーム (%4 == 0) だが未作成の stream 4 を
    使う (STREAM_NOT_FOUND 経路)
    """
    _client, server = _create_connection_pair()
    assert server.server_stream_priority(4, 5, False) is False


def test_http3_parse_priority_default() -> None:
    """キー省略時はデフォルト値 (urgency=3 / incremental=false) が適用される"""
    assert http3.parse_priority("") == (3, False)
    assert http3.parse_priority("i") == (3, True)
    assert http3.parse_priority("u=2") == (2, False)


def test_http3_parse_priority_values() -> None:
    """RFC 9218 の Priority ヘッダー値をパースできることを確認"""
    assert http3.parse_priority("u=2, i") == (2, True)
    assert http3.parse_priority("u=7") == (7, False)
    assert http3.parse_priority("u=0, i") == (0, True)


def test_http3_parse_priority_invalid() -> None:
    """不正な Priority ヘッダー値の扱いを確認

    RFC 9218 は out-of-range / unexpected type の無視を求めているが、
    nghttp3 のパーサはエラーを返すため None になる。長さ 1 以外の
    キーは未知パラメータとして無視されるため、デフォルト値を返す
    """
    assert http3.parse_priority("u=9") is None
    assert http3.parse_priority("i=1") is None
    # 未知の単一キーのみの辞書はパースに成功し、デフォルトのまま返る
    assert http3.parse_priority("not-a-dictionary") == (3, False)


def test_http3_set_max_client_streams_bidi_decrease_ignored() -> None:
    """累積最大数の減算が無視されることを確認

    減算ガードがないと nghttp3 の max_client_streams が減り、以降の
    PRIORITY_UPDATE が H3_ID_ERROR で拒否される
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    # 100 から 0 への減算は無視される (単調増加のみ許可)
    server.set_max_client_streams_bidi(0)

    assert client.client_stream_priority(0, 2, True) is True
    _pump(client, server)
    assert server.stream_priority(0) == (2, True)
