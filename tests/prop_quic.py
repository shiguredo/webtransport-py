"""QUIC Sans I/O API の Property-Based Testing"""

from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import quic

# Sans-IO テスト用の固定パスアドレス
CLIENT_ADDR = ("127.0.0.1", 50000)
SERVER_ADDR = ("127.0.0.1", 4433)


# uint64 の範囲
UINT64_MAX = 2**64 - 1


# Config フィールドの境界値テスト


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_streams_bidi(value: int):
    """max_streams_bidi に任意の uint64 値を設定できる"""
    config = quic.Config()
    config.max_streams_bidi = value
    assert config.max_streams_bidi == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_streams_uni(value: int):
    """max_streams_uni に任意の uint64 値を設定できる"""
    config = quic.Config()
    config.max_streams_uni = value
    assert config.max_streams_uni == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_data(value: int):
    """max_data に任意の uint64 値を設定できる"""
    config = quic.Config()
    config.max_data = value
    assert config.max_data == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_idle_timeout_ns(value: int):
    """idle_timeout_ns に任意の uint64 値を設定できる"""
    config = quic.Config()
    config.idle_timeout_ns = value
    assert config.idle_timeout_ns == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_datagram_frame_size(value: int):
    """max_datagram_frame_size に任意の uint64 値を設定できる"""
    config = quic.Config()
    config.max_datagram_frame_size = value
    assert config.max_datagram_frame_size == value


@given(st.lists(st.text(max_size=255), max_size=10))
@settings(max_examples=100)
def prop_config_alpn_protocols(values: list[str]):
    """alpn_protocols に任意の文字列リストを設定できる"""
    config = quic.Config()
    config.alpn_protocols = values
    assert config.alpn_protocols == values


@given(st.text(max_size=255))
@settings(max_examples=100)
def prop_config_server_name(value: str):
    """server_name に任意の文字列を設定できる"""
    config = quic.Config()
    config.server_name = value
    assert config.server_name == value


# Connection.receive() の堅牢性テスト


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_client_receive_arbitrary_data(data: bytes):
    """クライアント接続に任意のバイト列を渡してもクラッシュしない"""
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)

    # 任意のデータを受信してもクラッシュしない
    processed = conn.receive(data, CLIENT_ADDR, SERVER_ADDR)

    # 処理バイト数は入力長以下
    assert processed <= len(data)

    conn.close()


# ストリーム操作の堅牢性テスト


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_send_stream_data_arbitrary(data: bytes):
    """任意のデータをストリームに送信してもクラッシュしない"""
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)

    # ストリームを開く
    stream_id = conn.open_stream(True)

    # ハンドシェイク前でもクラッシュしない
    conn.send_stream_data(stream_id, data, False)

    conn.close()


# データグラム操作の堅牢性テスト


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_send_datagram_arbitrary(data: bytes):
    """任意のデータをデータグラムとして送信してもクラッシュしない"""
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.enable_datagram = True

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)

    # ハンドシェイク前でもクラッシュしない
    conn.send_datagram(data)

    conn.close()


# 接続クローズの堅牢性テスト


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_close_arbitrary_error_code(error_code: int):
    """任意のエラーコードで接続を閉じてもクラッシュしない"""
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    conn.close(error_code)

    assert conn.is_closed() is True


@given(st.text(max_size=255))
@settings(max_examples=100)
def prop_close_arbitrary_reason(reason: str):
    """任意の理由文字列で接続を閉じてもクラッシュしない"""
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    conn.close(0, reason)

    assert conn.is_closed() is True
