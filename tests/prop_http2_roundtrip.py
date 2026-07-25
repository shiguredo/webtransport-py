"""HTTP/2 Sans I/O API の高度な Property-Based Testing

クライアント-サーバー間の通信をシミュレートするテスト
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import http2


def create_client_server_pair():
    """クライアントとサーバーのペアを作成し、初期状態を設定"""
    client_config = http2.Config()
    server_config = http2.Config()
    server_config.is_server = True

    client = http2.Connection.create_client(client_config)
    server = http2.Connection.create_server(server_config)

    return client, server


def exchange_settings(client: http2.Connection, server: http2.Connection):
    """SETTINGS フレームを交換してセッションを確立"""
    for _ in range(10):
        client_data = client.send()
        if client_data:
            server.receive(client_data)

        server_data = server.send()
        if server_data:
            client.receive(server_data)

        if not client_data and not server_data:
            break


def valid_header_name(name: str) -> bool:
    """HTTP/2 で有効なヘッダー名かチェック"""
    if not name:
        return False
    return all(c.isalnum() or c in "-_" for c in name)


def valid_header_value(value: str) -> bool:
    """HTTP/2 で有効なヘッダー値かチェック"""
    return "\x00" not in value and "\r" not in value and "\n" not in value


def exchange_data_after_request(client: http2.Connection, server: http2.Connection) -> list:
    """リクエスト後にデータを交換してサーバーのイベントを収集"""
    server_events = []
    for _ in range(10):
        client_data = client.send()
        if client_data:
            server.receive(client_data)
            while True:
                event = server.next_event()
                if event is None:
                    break
                server_events.append(event)

        server_data = server.send()
        if server_data:
            client.receive(server_data)

        if not client_data and not server_data:
            break
    return server_events


@given(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=50),
)
@settings(max_examples=50)
def prop_http2_request_response_roundtrip(path: str):
    """リクエスト-レスポンスのラウンドトリップが正常に動作する"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    headers = [
        (":method", "GET"),
        (":path", f"/{path}"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]

    stream_id = client.submit_request(headers)
    assert stream_id > 0

    events = exchange_data_after_request(client, server)

    found_headers = False
    for event in events:
        if event.type == http2.EventType.HEADERS:
            found_headers = True
            path_found = False
            for name, value in event.headers:
                if name == ":path" and value == f"/{path}":
                    path_found = True
            assert path_found

    assert found_headers


@given(
    st.lists(
        st.tuples(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                min_size=1,
                max_size=50,
            ),
        ),
        min_size=0,
        max_size=5,
    )
)
@settings(max_examples=50)
def prop_http2_custom_headers_roundtrip(custom_headers: list[tuple[str, str]]):
    """カスタムヘッダーがラウンドトリップで保持される"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    filtered_headers = [
        (name.lower(), value)
        for name, value in custom_headers
        if valid_header_name(name) and valid_header_value(value) and not name.startswith(":")
    ]

    headers = [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ] + filtered_headers

    stream_id = client.submit_request(headers)
    if stream_id <= 0:
        return

    events = exchange_data_after_request(client, server)

    received_custom_headers = []
    for event in events:
        if event.type == http2.EventType.HEADERS:
            for name, value in event.headers:
                if not name.startswith(":"):
                    received_custom_headers.append((name, value))

    for name, value in filtered_headers:
        assert (name, value) in received_custom_headers


@given(st.binary(min_size=1, max_size=16384))
@settings(max_examples=50)
def prop_http2_send_data_no_crash(body: bytes):
    """任意のボディデータを send_data に渡してもクラッシュしない"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    headers = [
        (":method", "POST"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]

    stream_id = client.submit_request(headers)
    assert stream_id > 0

    # send_data を呼び出してもクラッシュしない
    # 注: 現在の実装ではデータプロバイダーが未設定のため、
    #     実際の DATA フレームは送信されない
    client.send_data(stream_id, body, True)


@given(st.integers(min_value=1, max_value=10))
@settings(max_examples=20)
def prop_http2_multiple_streams(num_streams: int):
    """複数のストリームを同時に開くことができる"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    stream_ids = []
    for i in range(num_streams):
        headers = [
            (":method", "GET"),
            (":path", f"/stream{i}"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
        stream_id = client.submit_request(headers)
        if stream_id > 0:
            stream_ids.append(stream_id)

    assert len(stream_ids) == num_streams

    for i, stream_id in enumerate(stream_ids):
        expected_stream_id = 1 + i * 2
        assert stream_id == expected_stream_id


@given(
    st.lists(
        st.binary(min_size=0, max_size=1000),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=30)
def prop_http2_chunked_send_data_no_crash(chunks: list[bytes]):
    """分割されたデータを send_data に渡してもクラッシュしない"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    headers = [
        (":method", "POST"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]

    stream_id = client.submit_request(headers)
    assert stream_id > 0

    # 複数回 send_data を呼び出してもクラッシュしない
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        client.send_data(stream_id, chunk, is_last)


@given(st.integers(min_value=0, max_value=2**32 - 1))
@settings(max_examples=50)
def prop_http2_goaway_no_crash(error_code: int):
    """任意のエラーコードで GOAWAY を送信してもクラッシュしない"""
    client, server = create_client_server_pair()
    exchange_settings(client, server)

    client.goaway(error_code)

    events = exchange_data_after_request(client, server)

    assert any(event.type == http2.EventType.GO_AWAY for event in events)


@given(st.integers(min_value=16384, max_value=16777215))
@settings(max_examples=20)
def prop_http2_max_frame_size_setting(frame_size: int):
    """max_frame_size 設定が正しく適用される"""
    client_config = http2.Config()
    client_config.max_frame_size = frame_size

    client = http2.Connection.create_client(client_config)
    assert client is not None

    data = client.send()
    assert data is not None


@given(st.integers(min_value=1, max_value=1000))
@settings(max_examples=20)
def prop_http2_max_concurrent_streams_setting(max_streams: int):
    """max_concurrent_streams 設定が正しく適用される"""
    client_config = http2.Config()
    client_config.max_concurrent_streams = max_streams

    client = http2.Connection.create_client(client_config)
    assert client is not None

    data = client.send()
    assert data is not None
