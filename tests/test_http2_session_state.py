"""HTTP/2 のセッション状態確認 API テスト"""

from __future__ import annotations

from webtransport import http2


def _exchange_settings(client: http2.Connection, server: http2.Connection) -> None:
    """SETTINGS フレームを交換してセッションを確立する

    双方の送信データが無くなるまで送信と受信を繰り返す
    """
    for _ in range(10):
        client_data = client.send()
        if client_data:
            server.receive(client_data)

        server_data = server.send()
        if server_data:
            client.receive(server_data)

        if not client_data and not server_data:
            break


def _create_connection_pair() -> tuple[http2.Connection, http2.Connection]:
    """クライアントとサーバーのペアを作成して SETTINGS を交換する

    @return (クライアント Connection, サーバー Connection)
    """
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_settings(client, server)
    return client, server


def _pump(src: http2.Connection, dst: http2.Connection) -> None:
    """src の送信データを全て dst に渡す

    send() は 1 回の呼び出しでフレームが無くなるまで返すとは限らない
    ため、送信データが無くなるまで繰り返す
    """
    for _ in range(10):
        data = src.send()
        if data:
            dst.receive(data)
        if not data:
            break


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http2_remote_settings_defaults() -> None:
    """ピアの SETTINGS が受信前にデフォルト値になることを確認"""
    client = http2.Connection.create_client(http2.Config())

    remote = client.remote_settings
    assert remote is not None
    # max_concurrent_streams のみセッション生成時に 100 が設定される
    # (他の SETTINGS は nghttp2 のデフォルト値のまま)
    assert remote["max_concurrent_streams"] == 100
    assert remote["initial_window_size"] == 65535
    assert remote["max_frame_size"] == 16384
    assert remote["max_header_list_size"] == 4294967295


def test_http2_local_settings_defaults() -> None:
    """ローカルの SETTINGS が ACK 前にデフォルト値になることを確認"""
    client = http2.Connection.create_client(http2.Config())

    local = client.local_settings
    assert local is not None
    # ピアの ACK 前は nghttp2 のデフォルト値 (max_concurrent_streams は
    # 上限なしの 4294967295)
    assert local["max_concurrent_streams"] == 4294967295
    assert local["initial_window_size"] == 65535
    assert local["max_frame_size"] == 16384
    assert local["max_header_list_size"] == 4294967295


def test_http2_settings_after_exchange() -> None:
    """SETTINGS 交換後にピア・ローカルの設定値が取得できることを確認"""
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    # デフォルト値と異なる値を設定し、受信で値が更新されることを明確にする
    server_config.initial_window_size = 131072
    server_config.max_header_list_size = 262144
    server = http2.Connection.create_server(server_config)
    _exchange_settings(client, server)

    # ピア (サーバー) の SETTINGS はサーバーの Http2Config の値になる
    # (max_concurrent_streams と max_frame_size はデフォルト値のまま)
    remote = client.remote_settings
    assert remote is not None
    assert remote["max_concurrent_streams"] == 100
    assert remote["initial_window_size"] == 131072
    assert remote["max_frame_size"] == 16384
    assert remote["max_header_list_size"] == 262144

    # ローカルの SETTINGS はピアの ACK 後に Http2Config の値になる
    local = client.local_settings
    assert local is not None
    assert local["max_concurrent_streams"] == 100
    assert local["initial_window_size"] == 65535
    assert local["max_frame_size"] == 16384
    assert local["max_header_list_size"] == 65536

    # サーバー側も同様にピア (クライアント) の設定値が取得できる
    remote_server = server.remote_settings
    assert remote_server is not None
    assert remote_server["max_concurrent_streams"] == 100
    assert remote_server["max_header_list_size"] == 65536

    # サーバーのローカル SETTINGS はクライアントの ACK 後に
    # サーバーの Http2Config の値になる
    local_server = server.local_settings
    assert local_server is not None
    assert local_server["initial_window_size"] == 131072
    assert local_server["max_header_list_size"] == 262144


def test_http2_outbound_queue_size() -> None:
    """送信キューのフレーム数が取得できることを確認"""
    client, _server = _create_connection_pair()

    # SETTINGS 交換後は送信キューが空
    assert client.outbound_queue_size == 0

    # リクエストを積むと送信キューにフレームが入る
    client.submit_request(_request_headers())
    assert client.outbound_queue_size >= 1

    # send でフレームが送出されると送信キューが空に戻る
    client.send()
    assert client.outbound_queue_size == 0


def test_http2_connection_window_sizes() -> None:
    """コネクションのウィンドウ残量が取得できることを確認"""
    client, server = _create_connection_pair()

    # 初期値はコネクションウィンドウサイズの 65535
    # (nghttp2 の NGHTTP2_INITIAL_CONNECTION_WINDOW_SIZE)
    assert client.remote_window_size == 65535
    assert client.local_window_size == 65535

    # サーバーがレスポンスデータを送信するとウィンドウ残量が減る
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"0123456789", False)

    # 送信前はウィンドウが減っていない
    assert server.remote_window_size == 65535

    _pump(server, client)

    # 送信側のリモートウィンドウと受信側のローカルウィンドウが
    # DATA 10 バイト分だけ減る
    assert server.remote_window_size == 65535 - 10
    assert client.local_window_size == 65535 - 10


def test_http2_stream_window_sizes() -> None:
    """ストリームのウィンドウ残量が取得できることを確認"""
    client, server = _create_connection_pair()

    # 存在しないストリームは None (ストリーム ID 3 は未使用の奇数 ID)
    assert client.stream_remote_window_size(3) is None
    assert client.stream_remote_window_size(-1) is None
    assert client.stream_local_window_size(3) is None

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0

    # リクエスト HEADERS が送出されるまではストリームが存在しない
    # (nghttp2 は HEADERS の送出時にストリームを開く)
    assert client.stream_remote_window_size(stream_id) is None

    _pump(client, server)

    # ストリームのウィンドウ残量は初期値の 65535
    # (SETTINGS_INITIAL_WINDOW_SIZE の値)
    assert client.stream_remote_window_size(stream_id) == 65535
    assert client.stream_local_window_size(stream_id) == 65535
    assert server.stream_local_window_size(stream_id) == 65535

    # サーバーがレスポンスデータを送信するとストリームの
    # リモートウィンドウが DATA 10 バイト分だけ減る
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"0123456789", False)
    _pump(server, client)
    assert server.stream_remote_window_size(stream_id) == 65535 - 10


def test_http2_effective_recv_data_length() -> None:
    """WINDOW_UPDATE 未送信の受信 DATA バイト数が取得できることを確認"""
    client, server = _create_connection_pair()

    # 受信前は 0
    assert client.effective_recv_data_length == 0

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # レスポンスデータを送信する (10 バイトはウィンドウの半分以下なので
    # WINDOW_UPDATE は送出されない)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"0123456789", False)
    _pump(server, client)

    # 受信データ量は WINDOW_UPDATE 未送信の間は受信バイト数のまま残る
    assert client.effective_recv_data_length == 10
    assert client.stream_effective_recv_data_length(stream_id) == 10

    # 存在しないストリームは None
    assert client.stream_effective_recv_data_length(3) is None


def test_http2_effective_recv_data_length_window_update_reset() -> None:
    """受信ウィンドウの半分以上に達すると WINDOW_UPDATE で 0 に戻ることを確認

    nghttp2 は受信ウィンドウの半分以上 (recv_window_size >=
    local_window_size / 2) に達すると自動で WINDOW_UPDATE をキュー投入して
    受信量を 0 に戻す (nghttp2 の nghttp2_should_send_window_update)。
    キュー投入は受信処理時に行われるため、受信直後から
    effective_recv_data_length は 0 になる
    """
    # 境界値の検証: 半分未満 (32766) は WINDOW_UPDATE がキュー投入されず、
    # 半分以上 (32767 と 32768) はキュー投入されて 0 に戻る。 WINDOW_UPDATE
    # は送出されるまで次のリセットを起こさないため、各ケースで独立した
    # 接続ペアを使う
    for sent, expected in [(32766, 32766), (32767, 0), (32768, 0)]:
        client, server = _create_connection_pair()

        stream_id = client.submit_request(_request_headers())
        assert stream_id > 0
        _pump(client, server)

        server.submit_response(stream_id, [(":status", "200")])
        server.send_data(stream_id, b"0" * sent, False)
        _pump(server, client)
        assert client.effective_recv_data_length == expected
        assert client.stream_effective_recv_data_length(stream_id) == expected

        if expected == 0:
            # WINDOW_UPDATE はクライアントの送信キューに積まれて送出待ちになる
            assert client.want_write() is True


def test_http2_request_allowed() -> None:
    """新しいリクエストの送信可否が取得できることを確認"""
    client, server = _create_connection_pair()

    # クライアントは送信可能、サーバーセッションは送信不可
    assert client.request_allowed is True
    assert server.request_allowed is False


def test_http2_stream_close() -> None:
    """ストリームの half-closed 状態が取得できることを確認"""
    client, server = _create_connection_pair()

    # 存在しないストリームは None (ストリーム ID 3 は未使用の奇数 ID)
    assert client.stream_local_close(3) is None
    assert client.stream_remote_close(3) is None

    # リクエストを送信する。データプロバイダを渡すため HEADERS には
    # END_STREAM が付かず、 send_data(..., eof=True) で終端する
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0

    # HEADERS 送出前はストリームが存在しないため None になる
    # (nghttp2 は HEADERS の送出時にストリームを開く)
    assert client.stream_local_close(stream_id) is None

    # eof=True の DATA を送出するとローカル側が half-closed になる
    client.send_data(stream_id, b"", eof=True)
    _pump(client, server)
    assert client.stream_local_close(stream_id) is True

    # ピアの END_STREAM を受信するとサーバーのリモート側が half-closed になる
    assert server.stream_remote_close(stream_id) is True
    assert server.stream_local_close(stream_id) is False

    # レスポンスの DATA を eof=True で送信してストリームの送信側を閉じる
    # (完了条件の send_data 経由の検証)。サーバーは既にリクエストの
    # END_STREAM を受信しているため、 DATA 送出と同時に両方向が閉じて
    # ストリームが nghttp2 の管理から外れ、 stream_local_close は True では
    # なく None になる (half-closed の True はクライアント側の eof=True
    # DATA 送出で検証済み)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response", True)
    _pump(server, client)
    assert server.stream_local_close(stream_id) is None
    assert server.stream_remote_close(stream_id) is None
    assert server.stream_remote_window_size(stream_id) is None
    assert server.stream_local_window_size(stream_id) is None
    assert server.stream_effective_recv_data_length(stream_id) is None
    assert client.stream_remote_close(stream_id) is None


def test_http2_closed_connection_returns_none() -> None:
    """コネクションが閉じた場合に全ての getter が None を返すことを確認"""
    client, server = _create_connection_pair()

    # GOAWAY 送信後は閉鎖扱いにならず getter は値を返し続ける
    client.goaway()
    _pump(client, server)
    assert client.is_closed() is False
    assert client.remote_window_size == 65535
    # request_allowed は GOAWAY 送信後に False になる (アクティブストリーム
    # が無い場合、 GOAWAY 送信後に want_read / want_write が 0 になり
    # セッションが終了状態になるため)
    assert client.request_allowed is False

    # サーバーは GOAWAY を受信すると閉鎖扱いになる
    assert server.is_closed() is True
    assert server.remote_settings is None
    assert server.local_settings is None
    assert server.outbound_queue_size is None
    assert server.remote_window_size is None
    assert server.local_window_size is None
    assert server.effective_recv_data_length is None
    assert server.request_allowed is None
    assert server.stream_remote_window_size(1) is None
    assert server.stream_local_window_size(1) is None
    assert server.stream_effective_recv_data_length(1) is None
    assert server.stream_local_close(1) is None
    assert server.stream_remote_close(1) is None
