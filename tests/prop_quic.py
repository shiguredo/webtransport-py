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
    result = conn.receive(data, CLIENT_ADDR, SERVER_ADDR)

    # 受理・破棄・終了のいずれかになる
    assert result in (
        quic.ReceiveResult.ACCEPTED,
        quic.ReceiveResult.DISCARDED,
        quic.ReceiveResult.CLOSED,
    )

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


# ========== トランスポートパラメータ Config の生成時検証テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**64 - 2),
)
@settings(max_examples=100)
def prop_config_transport_params_valid_no_abort(
    max_data: int,
    max_stream_data_bidi_local: int,
    max_stream_data_bidi_remote: int,
    max_stream_data_uni: int,
    max_streams_bidi: int,
    max_streams_uni: int,
    max_datagram_frame_size: int,
    idle_timeout_ns: int,
):
    """有効範囲内のトランスポートパラメータで接続生成が abort しない

    フロー制御値は RFC 9000 Section 16 の varint 上限 (2^62 - 1) 以下、
    idle_timeout_ns は UINT64_MAX 未満が有効範囲である。max_streams 系と
    max_datagram_frame_size もエンコード経路の assert に到達するため、
    同じ上限で検証する。依存ライブラリは Release ビルドでも assert が
    有効なため、バインディング側で事前検証する
    """
    # 有効範囲内の値を設定する
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.max_data = max_data
    config.max_stream_data_bidi_local = max_stream_data_bidi_local
    config.max_stream_data_bidi_remote = max_stream_data_bidi_remote
    config.max_stream_data_uni = max_stream_data_uni
    config.max_streams_bidi = max_streams_bidi
    config.max_streams_uni = max_streams_uni
    config.max_datagram_frame_size = max_datagram_frame_size
    config.idle_timeout_ns = idle_timeout_ns

    # 生成が成功する (abort も RuntimeError も起きない)
    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    conn.close()


@given(
    st.integers(min_value=2**62, max_value=UINT64_MAX),
)
@settings(max_examples=100)
def prop_config_max_data_invalid_raises(max_data: int):
    """varint 上限超えの max_data では接続生成が RuntimeError になる"""
    # 上限超えの値を設定する
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.max_data = max_data

    # 生成失敗は RuntimeError で扱う (abort しない)
    try:
        conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    except RuntimeError:
        return
    conn.close()
    raise AssertionError("上限超えの max_data で生成失敗しませんでした")


@given(st.integers(min_value=2**62, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_streams_datagram_invalid_raises(value: int):
    """varint 上限超えのストリーム数・データグラム値では生成が RuntimeError になる

    max_streams_bidi / max_streams_uni / max_datagram_frame_size のいずれか
    1 つに上限超えを設定し、生成失敗が RuntimeError で扱われる (abort
    しない) ことを検証する。エンコード経路の assert に到達するため、
    フロー制御値と同じ上限で検証する
    """
    # 上限超えの値を各フィールドに設定して生成する
    for field in (
        "max_streams_bidi",
        "max_streams_uni",
        "max_datagram_frame_size",
    ):
        config = quic.Config()
        config.alpn_protocols = ["h3"]
        config.server_name = "localhost"
        setattr(config, field, value)

        # 生成失敗は RuntimeError で扱う (abort しない)
        try:
            conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
        except RuntimeError:
            continue
        conn.close()
        raise AssertionError("上限超えの値で生成失敗しませんでした")


def test_config_idle_timeout_max_raises() -> None:
    """idle_timeout_ns が UINT64_MAX では接続生成が RuntimeError になる"""
    # UINT64_MAX は ngtcp2 の assert に到達するため拒否される
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.idle_timeout_ns = UINT64_MAX

    # 生成失敗は RuntimeError で扱う (abort しない)
    try:
        conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    except RuntimeError:
        return
    conn.close()
    raise AssertionError("UINT64_MAX の idle_timeout_ns で生成失敗しませんでした")
