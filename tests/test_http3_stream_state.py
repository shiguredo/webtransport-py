"""HTTP/3 のストリーム状態確認 API テスト"""

from conftest import _create_http3_connection_pair, _http3_pump


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http3_stream_writable() -> None:
    """データストリームの書き込み可否が取得できることを確認"""
    client, _server = _create_http3_connection_pair()

    # リクエストを送信すると書き込み可能になる
    assert client.submit_request(0, _request_headers()) is True
    assert client.stream_writable(0) == 1

    # 存在しないストリームは書き込み不可
    assert client.stream_writable(999) == 0


def test_http3_stream_flushed() -> None:
    """送信データが QUIC スタックに受け渡し済みか確認できることを確認"""
    client, server = _create_http3_connection_pair()

    # リクエストを送信してデータを積む
    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"request-body", fin=True)

    # 送信処理前は QUIC スタックに受け渡し済みでない
    assert client.stream_flushed(0) == 0

    # 送信処理で QUIC スタックに受け渡すと受け渡し済みになる
    _http3_pump(client, server)
    assert client.stream_flushed(0) == 1

    # 存在しないストリームは受け渡し済み扱い (1) になる
    assert client.stream_flushed(999) == 1


def test_http3_frame_payload_left() -> None:
    """受信中フレームのペイロード残量が取得できることを確認"""
    client, server = _create_http3_connection_pair()

    # リクエストのヘッダーを送信する
    assert client.submit_request(0, _request_headers()) is True
    _http3_pump(client, server)

    # ボディを送信し、DATA フレームを 4 バイトずつ受信する
    # 受信途中のフレーム残量が受信済みバイト数に応じて単調に減り、
    # 最後のチャンクでフレームが完了して残量が 0 になることを確認する。
    # DATA フレームは 2 バイトのヘッダー + 16 バイトのペイロードで 18 バイト
    # のため、4 バイト刻みのチャンクは 5 個になり、残量は 14 → 10 → 6 → 2 → 0
    # と減る (get_streams_to_send は 1 回の呼び出しでフレーム全体を返す)
    client.send_data(0, b"0123456789abcdef", fin=True)
    streams = client.get_streams_to_send()
    assert streams, "送信データがありません"
    for stream_id, data, fin in streams:
        # 送信データはリクエストストリーム (stream 0) に限定される
        assert stream_id == 0
        previous_left: int | None = None
        for i in range(0, len(data), 4):
            server.receive_stream_data(0, data[i : i + 4], False)
            left = server.frame_payload_left(0)
            assert left is not None
            if previous_left is not None:
                # 受信するたびに残量が減る (単調減少)
                assert left < previous_left
            previous_left = left
        # 最後のチャンクでフレームが完了し残量が 0 になる
        assert previous_left == 0
        server.receive_stream_data(0, b"", fin)
        assert server.frame_payload_left(0) == 0


def test_http3_frame_payload_left_guard() -> None:
    """範囲外の stream_id で frame_payload_left がクラッシュしないことを確認

    nghttp3 は assert で stream_id の範囲を検証するため、C++ 側のガードで
    範囲外の値には 0 を返す
    """
    client, _server = _create_http3_connection_pair()
    assert client.frame_payload_left(-1) == 0
    # NGHTTP3_MAX_VARINT (2**62 - 1) を超える値もガードされる
    assert client.frame_payload_left(1 << 62) == 0


def test_http3_drained() -> None:
    """ドレイン状態が取得できることを確認 (サーバーのみ)"""
    client, server = _create_http3_connection_pair()

    # クライアントセッションでは None
    assert client.drained is None

    # リクエストを送信する
    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"request-body", fin=True)
    _http3_pump(client, server)

    # レスポンスを送信する
    assert server.submit_response(0, [(":status", "200")]) is True
    server.send_data(0, b"response-body", fin=True)
    _http3_pump(server, client)

    # goaway 前はドレイン状態でない
    assert server.drained is False

    # リモートストリームの終了を nghttp3 に伝えてから goaway すると
    # ドレイン状態になる (QUIC 層のストリーム終了を模した操作)
    server.close_stream(0, 0)
    server.goaway()
    # GOAWAY フレームを書き出す送信処理が完了するまではドレイン状態でない
    assert server.drained is False
    _http3_pump(server, client)
    assert server.drained is True
