"""HTTP/3 Sans I/O API の Property-Based Testing"""

from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import http3


# uint64 の範囲
UINT64_MAX = 2**64 - 1


# Config フィールドの境界値テスト


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_field_section_size(value: int):
    """max_field_section_size に任意の uint64 値を設定できる"""
    config = http3.Config()
    config.max_field_section_size = value
    assert config.max_field_section_size == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_qpack_max_dtable_capacity(value: int):
    """qpack_max_dtable_capacity に任意の uint64 値を設定できる"""
    config = http3.Config()
    config.qpack_max_dtable_capacity = value
    assert config.qpack_max_dtable_capacity == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_enable_webtransport(value: bool):
    """enable_webtransport に任意の bool 値を設定できる"""
    config = http3.Config()
    config.enable_webtransport = value
    assert config.enable_webtransport == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_enable_h3_datagram(value: bool):
    """enable_h3_datagram に任意の bool 値を設定できる"""
    config = http3.Config()
    config.enable_h3_datagram = value
    assert config.enable_h3_datagram == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_is_server(value: bool):
    """is_server に任意の bool 値を設定できる"""
    config = http3.Config()
    config.is_server = value
    assert config.is_server == value


# Connection.receive_stream_data() の堅牢性テスト


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=65536), st.booleans())
@settings(max_examples=100)
def prop_client_receive_stream_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """クライアント接続に任意のストリームデータを渡してもクラッシュしない"""
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 任意のストリームデータを受信してもクラッシュしない
    processed = conn.receive_stream_data(stream_id, data, fin)

    # 処理バイト数は入力長以下
    assert processed <= len(data)


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=65536), st.booleans())
@settings(max_examples=100)
def prop_server_receive_stream_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """サーバー接続に任意のストリームデータを渡してもクラッシュしない"""
    config = http3.Config()
    config.is_server = True
    conn = http3.Connection.create_server(config)

    # 任意のストリームデータを受信してもクラッシュしない
    processed = conn.receive_stream_data(stream_id, data, fin)

    # 処理バイト数は入力長以下
    assert processed <= len(data)


# ストリームデータ送信の堅牢性テスト


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=65536), st.booleans())
@settings(max_examples=100)
def prop_send_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """任意のデータをストリームに送信してもクラッシュしない"""
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 任意のデータを送信してもクラッシュしない
    conn.send_data(stream_id, data, fin)


# submit_request の堅牢性テスト


@given(st.lists(st.tuples(st.text(max_size=100), st.text(max_size=100)), max_size=20))
@settings(max_examples=100)
def prop_submit_request_arbitrary_headers(headers: list[tuple[str, str]]):
    """任意のヘッダーでリクエストを送信してもクラッシュしない"""
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # QPACK ストリームがバインドされていない場合は false を返す
    result = conn.submit_request(0, headers)
    assert result is False


# goaway の堅牢性テスト


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_goaway_arbitrary_id(id: int):
    """任意の ID で goaway を呼び出してもクラッシュしない"""
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # コントロールストリームがバインドされていない場合は何もしない
    conn.goaway(id)
