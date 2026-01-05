"""HTTP/2 Sans I/O API の Property-Based Testing"""

from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import http2


# uint32 の範囲
UINT32_MAX = 2**32 - 1


# Config フィールドの境界値テスト


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_initial_window_size(value: int):
    """initial_window_size に任意の uint32 値を設定できる"""
    config = http2.Config()
    config.initial_window_size = value
    assert config.initial_window_size == value


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_max_concurrent_streams(value: int):
    """max_concurrent_streams に任意の uint32 値を設定できる"""
    config = http2.Config()
    config.max_concurrent_streams = value
    assert config.max_concurrent_streams == value


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_max_frame_size(value: int):
    """max_frame_size に任意の uint32 値を設定できる"""
    config = http2.Config()
    config.max_frame_size = value
    assert config.max_frame_size == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_is_server(value: bool):
    """is_server に任意の bool 値を設定できる"""
    config = http2.Config()
    config.is_server = value
    assert config.is_server == value


# Connection.receive() の堅牢性テスト


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_client_receive_arbitrary_data(data: bytes):
    """クライアント接続に任意のバイト列を渡してもクラッシュしない"""
    config = http2.Config()
    conn = http2.Connection.create_client(config)

    # SETTINGS を送信
    conn.send()

    # 任意のデータを受信してもクラッシュしない
    processed = conn.receive(data)

    # 処理バイト数は入力長以下
    assert processed <= len(data)


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_server_receive_arbitrary_data(data: bytes):
    """サーバー接続に任意のバイト列を渡してもクラッシュしない"""
    config = http2.Config()
    conn = http2.Connection.create_server(config)

    # SETTINGS を送信
    conn.send()

    # 任意のデータを受信してもクラッシュしない
    processed = conn.receive(data)

    # 処理バイト数は入力長以下
    assert processed <= len(data)


# ヘッダー操作の堅牢性テスト


@given(st.lists(st.tuples(st.text(max_size=100), st.text(max_size=100)), max_size=20))
@settings(max_examples=100)
def prop_submit_request_arbitrary_headers(headers: list[tuple[str, str]]):
    """任意のヘッダーでリクエストを送信してもクラッシュしない"""
    config = http2.Config()
    conn = http2.Connection.create_client(config)

    # SETTINGS を送信
    conn.send()

    # 任意のヘッダーでリクエストを送信
    # エラーになる可能性があるが、クラッシュしない
    stream_id = conn.submit_request(headers)

    # stream_id は正または負（エラー時）
    assert isinstance(stream_id, int)


# ストリームデータの堅牢性テスト


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_send_data_arbitrary(data: bytes):
    """任意のデータをストリームに送信してもクラッシュしない"""
    config = http2.Config()
    conn = http2.Connection.create_client(config)

    # SETTINGS を送信
    conn.send()

    # リクエストを送信してストリームを開く
    headers = [
        (":method", "POST"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    stream_id = conn.submit_request(headers)

    if stream_id > 0:
        # 任意のデータを送信
        conn.send_data(stream_id, data, False)


# GOAWAY の堅牢性テスト


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_goaway_arbitrary_error_code(error_code: int):
    """任意のエラーコードで GOAWAY を送信してもクラッシュしない"""
    config = http2.Config()
    conn = http2.Connection.create_client(config)

    # SETTINGS を送信
    conn.send()

    # 任意のエラーコードで GOAWAY
    conn.goaway(error_code)
