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


# ========== Config の生成時検証テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
)
@settings(max_examples=100)
def prop_config_valid_no_abort(
    max_field_section_size: int,
    qpack_max_dtable_capacity: int,
    qpack_blocked_streams: int,
):
    """有効範囲内の Config 値で接続生成が abort しない

    3 値は RFC 9000 Section 16 の varint 上限 (2^62 - 1) 以下が有効範囲で
    ある。依存ライブラリは Release ビルドでも assert が有効なため、
    バインディング側で事前検証する
    """
    # 有効範囲内の値を設定する
    config = http3.Config()
    config.max_field_section_size = max_field_section_size
    config.qpack_max_dtable_capacity = qpack_max_dtable_capacity
    config.qpack_blocked_streams = qpack_blocked_streams

    # クライアントとサーバーのどちらも生成が成功する
    client = http3.Connection.create_client(config)
    assert client is not None
    server_config = http3.Config()
    server_config.is_server = True
    server_config.max_field_section_size = max_field_section_size
    server_config.qpack_max_dtable_capacity = qpack_max_dtable_capacity
    server_config.qpack_blocked_streams = qpack_blocked_streams
    server = http3.Connection.create_server(server_config)
    assert server is not None


@given(st.integers(min_value=2**62, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_invalid_raises(value: int):
    """varint 上限超えの Config 値では生成が RuntimeError になる

    3 フィールドのいずれか 1 つに上限超えを設定し、生成失敗が
    RuntimeError で扱われる (abort しない) ことを検証する
    """
    # 上限超えの値を各フィールドに設定して生成する
    for field in (
        "max_field_section_size",
        "qpack_max_dtable_capacity",
        "qpack_blocked_streams",
    ):
        config = http3.Config()
        setattr(config, field, value)

        # 生成失敗は RuntimeError で扱う (abort しない)
        try:
            http3.Connection.create_client(config)
        except RuntimeError:
            continue
        raise AssertionError("上限超えの値で生成失敗しませんでした")


# ========== stream_id 全域の堅牢性テスト ==========


@given(st.integers(), st.binary(max_size=1024), st.booleans())
@settings(max_examples=100)
def prop_receive_stream_data_arbitrary_id_no_abort(stream_id: int, data: bytes, fin: bool):
    """整数全域の stream_id で receive_stream_data しても abort しない

    負値や varint 上限 (2^62 - 1) 超えは ValueError で拒否し、有効範囲内は
    処理バイト数を返す。いずれも nghttp3 の assert に到達しない
    """
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 不正 ID は ValueError、有効 ID は処理バイト数を返す。int64 に収まら
    # ない値は binding 境界で TypeError になるが、いずれも abort しない
    try:
        processed = conn.receive_stream_data(stream_id, data, fin)
    except ValueError, TypeError, OverflowError:
        return
    assert processed <= len(data)


@given(st.integers())
@settings(max_examples=100)
def prop_submit_request_arbitrary_id_no_abort(stream_id: int):
    """整数全域の stream_id で submit_request しても abort しない

    QPACK 未バインドまたは非自起点 ID では False を返す。Python int が
    int64 に収まらない値は nanobind 側で OverflowError になり得るため、
    その場合も abort しない正常系として受け付ける
    """
    config = http3.Config()
    conn = http3.Connection.create_client(config)
    conn.bind_control_stream(2)
    conn.bind_qpack_encoder_stream(6)
    conn.bind_qpack_decoder_stream(10)

    # 成功・失敗のいずれも bool で返る (abort しない)。int64 に収まら
    # ない値は binding 境界で TypeError になる
    try:
        result = conn.submit_request(stream_id, [(":method", "GET")])
    except ValueError, TypeError, OverflowError:
        return
    assert result is False or result is True


@given(st.integers())
@settings(max_examples=100)
def prop_bind_control_stream_arbitrary_id_no_abort(stream_id: int):
    """整数全域の stream_id で bind_control_stream しても abort しない

    不正 ID は ValueError で拒否し、有効な単方向ストリーム ID は黙って
    受け付ける。Python int が int64 に収まらない値は OverflowError に
    なり得るため、その場合も正常系として受け付ける
    """
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # ValueError または受け付けのいずれかで abort しない。int64 に収まら
    # ない値は binding 境界で TypeError になる
    try:
        conn.bind_control_stream(stream_id)
    except ValueError, TypeError, OverflowError:
        return


def test_submit_response_without_qpack_returns_false() -> None:
    """QPACK 未バインドの submit_response が False を返す"""
    # QPACK ストリームをバインドしていないサーバー接続を用意する
    config = http3.Config()
    config.is_server = True
    conn = http3.Connection.create_server(config)

    # nghttp3 の assert に到達せず False で拒否される
    assert conn.submit_response(0, [(":status", "200")]) is False


@given(st.integers())
@settings(max_examples=100)
def prop_bind_qpack_streams_arbitrary_id_no_abort(stream_id: int):
    """整数全域の stream_id で QPACK バインドしても abort しない

    不正 ID は黙って無視し、有効な単方向ストリーム ID は受け付ける。
    未検証のまま nghttp3 へ渡すと assert に到達するため、バインディング側
    で検証する。int64 に収まらない値は binding 境界で TypeError になるが、
    いずれも abort しない
    """
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 不正 ID の無視または受け付けのいずれかで abort しない
    try:
        conn.bind_qpack_encoder_stream(stream_id)
        conn.bind_qpack_decoder_stream(stream_id)
    except ValueError, TypeError, OverflowError:
        return


def test_receive_stream_data_invalid_id_raises_value_error() -> None:
    """範囲外 ID の receive_stream_data が ValueError になる"""
    # 負値と varint 上限 (2^62 - 1) 超えは nghttp3 の assert に到達する前に
    # ValueError で拒否される
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 不正 ID はいずれも ValueError になる (abort しない)
    for bad_id in (-1, 2**62):
        try:
            conn.receive_stream_data(bad_id, b"data", False)
        except ValueError:
            continue
        raise AssertionError("範囲外 ID で ValueError になりませんでした")


def test_set_max_client_streams_bidi_decrease_ignored() -> None:
    """set_max_client_streams_bidi の減少値が黙って無視される"""
    # サーバー接続で累積最大数を 100 に設定する
    config = http3.Config()
    config.is_server = True
    conn = http3.Connection.create_server(config)
    conn.set_max_client_streams_bidi(100)

    # 減少値は nghttp3 の assert に到達せず黙って無視される (abort しない)
    conn.set_max_client_streams_bidi(50)


def test_reset_stream_invalid_id_no_abort() -> None:
    """範囲外 ID の reset_stream が黙って無視される"""
    # 負値と varint 上限 (2^62 - 1) 超えは nghttp3 の assert に到達する前に
    # 黙って無視される (send_data 等と同じ契約)
    config = http3.Config()
    conn = http3.Connection.create_client(config)

    # 不正 ID はいずれも例外なく無視される (abort しない)
    for bad_id in (-1, 2**62):
        conn.reset_stream(bad_id, 0)


def test_receive_stream_data_bound_uni_no_abort() -> None:
    """バインド済みの自単方向ストリームへの受信が黙って無視される"""
    # クライアントとサーバーで自起点の制御・QPACK ストリームをバインドする
    client = http3.Connection.create_client(http3.Config())
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # 自単方向ストリームは送信専用のため、受信しても nghttp3 の assert に
    # 到達せず黙って無視される (abort しない)
    for stream_id in (2, 6, 10):
        assert client.receive_stream_data(stream_id, b"data", False) == 0
    for stream_id in (3, 7, 11):
        assert server.receive_stream_data(stream_id, b"data", False) == 0


def test_goaway_twice_no_abort() -> None:
    """goaway の二重呼び出しが黙って無視される"""
    # サーバー接続で制御ストリームをバインドして GOAWAY を送出する
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.goaway()

    # 新規双方向ストリームの受信で受信済み最大値が進んでも、二重の goaway は
    # nghttp3 の assert に到達せず黙って無視される (abort しない)
    client = http3.Connection.create_client(http3.Config())
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    assert client.submit_request(4, [(":method", "GET")]) is True
    for stream_id, data, fin in client.get_streams_to_send():
        server.receive_stream_data(stream_id, data, fin)
    server.goaway()

    # 無視されただけで接続は継続する
    assert server.is_closed() is False


def test_shutdown_notice_then_goaway_no_abort() -> None:
    """shutdown notice 後の goaway が abort しない"""
    # shutdown notice (GOAWAY ID 2^62 - 4) 送信後に goaway (受信済み最大値
    # 基準の小さい ID) を送る RFC 9114 Section 5.2 の正規フローが機能する
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # shutdown notice の送出に成功する
    assert server.submit_shutdown_notice() is True
    # 後続の goaway も abort せず、接続は継続する
    server.goaway()
    assert server.is_closed() is False
    # notice 済みのため二重の notice は拒否される (goaway 済みでもあるため、
    # いずれのガードでも False になる)
    assert server.submit_shutdown_notice() is False
