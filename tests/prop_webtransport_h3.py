"""WebTransport over HTTP/3 Sans I/O API の Property-Based Testing"""

from conftest import _drain_events, _encode_wt_datagram, _establish_session
from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import h3
from webtransport.h3._error_codes import (
    deliver_stream_reset_error_code,
    webtransport_code_to_http_code,
)

# uint64 の範囲
UINT64_MAX = 2**64 - 1


# ========== Config フィールドの境界値テスト ==========


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_max_field_section_size(value: int):
    """max_field_section_size に任意の uint64 値を設定できる"""
    config = h3.Config()
    config.max_field_section_size = value
    assert config.max_field_section_size == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_qpack_max_dtable_capacity(value: int):
    """qpack_max_dtable_capacity に任意の uint64 値を設定できる"""
    config = h3.Config()
    config.qpack_max_dtable_capacity = value
    assert config.qpack_max_dtable_capacity == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_qpack_blocked_streams(value: int):
    """qpack_blocked_streams に任意の uint64 値を設定できる"""
    config = h3.Config()
    config.qpack_blocked_streams = value
    assert config.qpack_blocked_streams == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_is_server(value: bool):
    """is_server に任意の bool 値を設定できる"""
    config = h3.Config()
    config.is_server = value
    assert config.is_server == value


# ========== Session 作成の堅牢性テスト ==========


def prop_session_create_client():
    """クライアントセッションが作成できる"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    assert session is not None
    assert session.is_closed() is False


def prop_session_create_server():
    """サーバーセッションが作成できる"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    assert session is not None
    assert session.is_closed() is False


# ========== receive_stream_data の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=65536), st.booleans())
@settings(max_examples=100)
def prop_client_receive_stream_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """クライアントセッションに任意のストリームデータを渡してもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    processed = session.receive_stream_data(stream_id, data, fin)
    assert processed <= len(data)


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=65536), st.booleans())
@settings(max_examples=100)
def prop_server_receive_stream_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """サーバーセッションに任意のストリームデータを渡してもクラッシュしない"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    processed = session.receive_stream_data(stream_id, data, fin)
    assert processed <= len(data)


# ========== receive_datagram の堅牢性テスト ==========


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_client_receive_datagram_arbitrary(data: bytes):
    """クライアントセッションに任意のデータグラムを渡してもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.receive_datagram(data)


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_server_receive_datagram_arbitrary(data: bytes):
    """サーバーセッションに任意のデータグラムを渡してもクラッシュしない"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    session.receive_datagram(data)


# ========== bind_*_stream の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_bind_control_stream_arbitrary(stream_id: int):
    """任意のストリーム ID でコントロールストリームをバインドしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.bind_control_stream(stream_id)


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_bind_qpack_encoder_stream_arbitrary(stream_id: int):
    """任意のストリーム ID で QPACK エンコーダーストリームをバインドしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.bind_qpack_encoder_stream(stream_id)


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_bind_qpack_decoder_stream_arbitrary(stream_id: int):
    """任意のストリーム ID で QPACK デコーダーストリームをバインドしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.bind_qpack_decoder_stream(stream_id)


# ========== connect の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/:.-", max_size=200),
)
@settings(max_examples=100)
def prop_connect_arbitrary(stream_id: int, url: str):
    """任意のストリーム ID と URL で connect してもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.connect(stream_id, url)


# ========== accept_session / reject_session の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_accept_session_arbitrary(stream_id: int):
    """任意のストリーム ID で accept_session してもクラッシュしない"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    session.accept_session(stream_id)


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=100)
def prop_reject_session_arbitrary(stream_id: int, status_code: int):
    """任意のパラメータで reject_session してもクラッシュしない"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    session.reject_session(stream_id, status_code)


# ========== open_stream の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.booleans(),
)
@settings(max_examples=100)
def prop_open_stream_arbitrary(session_id: int, stream_id: int, is_unidirectional: bool):
    """任意のパラメータでストリームをオープンしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    result = session.open_stream(session_id, stream_id, is_unidirectional)
    # セッションが存在しない場合は False を返す
    assert result is False


# ========== send_stream_data の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.binary(max_size=65536),
    st.booleans(),
)
@settings(max_examples=100)
def prop_send_stream_data_arbitrary(stream_id: int, data: bytes, fin: bool):
    """任意のデータをストリームに送信してもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.send_stream_data(stream_id, data, fin)


# ========== send_datagram の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1), st.binary(max_size=1200))
@settings(max_examples=100)
def prop_send_datagram_arbitrary(session_id: int, data: bytes):
    """任意のセッション ID へのデータグラム送信がクラッシュしないことを確認

    セッション終了の MUST (draft-ietf-webtrans-http3-16 Section 6) により、
    確立済みセッション ID への送信は送出され、session_ids_ に含まれない
    セッション ID への送信は黙って無視される。いずれもクラッシュしない
    """
    client, _server, established_session_id = _establish_session()
    # 確立済みセッション ID への送信は送出される (session_id が確立済み
    # ID と衝突する場合は 2 件分積まれる)
    client.send_datagram(established_session_id, data)
    # 任意のセッション ID (未確立・終了済みを含む) への送信もクラッシュしない
    client.send_datagram(session_id, data)
    # 確立済み ID への送信がキューに現れることを確認する
    assert _encode_wt_datagram(established_session_id, data) in client.get_datagrams_to_send()


# ========== close_stream の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1), st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_close_stream_arbitrary(stream_id: int, error_code: int):
    """任意のストリームをクローズしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.close_stream(stream_id, error_code)


# ========== close_session の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
    st.text(max_size=100),
)
@settings(max_examples=100)
def prop_close_session_arbitrary(session_id: int, error_code: int, reason: str):
    """任意のセッションをクローズしてもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session.close_session(session_id, error_code, reason)


# ========== get 系メソッドの堅牢性テスト ==========


def prop_get_required_streams():
    """get_required_streams が正しく動作する"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    required = session.get_required_streams()
    assert isinstance(required, list)
    assert len(required) == 3  # control, qpack_encoder, qpack_decoder


def prop_get_session_ids():
    """get_session_ids が正しく動作する"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    session_ids = session.get_session_ids()
    assert isinstance(session_ids, list)
    assert len(session_ids) == 0  # 初期状態は空


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_get_session_streams_arbitrary(session_id: int):
    """任意のセッション ID で get_session_streams してもクラッシュしない"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    streams = session.get_session_streams(session_id)
    assert isinstance(streams, list)


def prop_get_datagrams_to_send():
    """get_datagrams_to_send が正しく動作する"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    datagrams = session.get_datagrams_to_send()
    assert isinstance(datagrams, list)
    assert len(datagrams) == 0  # 初期状態は空


def prop_get_streams_to_send():
    """get_streams_to_send が正しく動作する"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    streams = session.get_streams_to_send()
    assert isinstance(streams, list)
    assert len(streams) == 0  # 初期状態は空


# ========== next_event の堅牢性テスト ==========


def prop_next_event_returns_none():
    """初期状態で next_event が None を返す"""
    config = h3.Config()
    session = h3.Session.create_client(config)
    event = session.next_event()
    assert event is None


# ========== set_max_client_streams_bidi の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=100)
def prop_set_max_client_streams_bidi_arbitrary(max_streams: int):
    """任意の値で set_max_client_streams_bidi してもクラッシュしない"""
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    session.set_max_client_streams_bidi(max_streams)


# ========== close_session のエラーメッセージ切り詰め (draft-16 Section 6) ==========


@given(st.text(min_size=1025, max_size=2000))
@settings(max_examples=100)
def prop_close_session_error_message_utf8_safe(message: str):
    """任意のエラーメッセージの close_session が UTF-8 境界で切り詰められて届く

    draft-16 Section 6 の MUST「Senders that truncate an application-supplied
    message MUST do so at a UTF-8 character boundary」「its length MUST NOT
    exceed 1024 bytes」に従い、message の内容によらずピアへ届く Application
    Error Message が well-formed UTF-8 で 1024 バイト以下になることを検証する。
    入力はバイト長 1024 を超える文字列のみとし、切り詰めが発生する経路を
    必ず通す (st.text の分布は小さな文字列に偏るため)。書き出されたストリーム
    データをそのままピアに渡し、SessionClosed が正常に届くこと (不正な
    Application Error Message を拒否しないこと) を最大の不変条件とする。
    """
    client, server, session_id = _establish_session()
    server.close_session(session_id, 0, message)
    for stream_id, data, fin in server.get_streams_to_send():
        client.receive_stream_data(stream_id, data, fin)

    closed_events = [
        event for event in _drain_events(client) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    received = closed_events[0].error_message
    assert closed_events[0].error_code == 0
    # 送信したメッセージの先頭部分 (文字境界で切れた整数バイト数) と一致する
    assert message.startswith(received)
    assert len(received.encode("utf-8")) <= 1024


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
    """有効範囲内の Config 値でセッション生成が abort しない

    3 値は RFC 9000 Section 16 の varint 上限 (2^62 - 1) 以下が有効範囲で
    ある。依存ライブラリは Release ビルドでも assert が有効なため、
    バインディング側で事前検証する
    """
    # 有効範囲内の値を設定する
    config = h3.Config()
    config.max_field_section_size = max_field_section_size
    config.qpack_max_dtable_capacity = qpack_max_dtable_capacity
    config.qpack_blocked_streams = qpack_blocked_streams

    # クライアントとサーバーのどちらも生成が成功する
    client = h3.Session.create_client(config)
    assert client is not None
    server_config = h3.Config()
    server_config.is_server = True
    server_config.max_field_section_size = max_field_section_size
    server_config.qpack_max_dtable_capacity = qpack_max_dtable_capacity
    server_config.qpack_blocked_streams = qpack_blocked_streams
    server = h3.Session.create_server(server_config)
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
        config = h3.Config()
        setattr(config, field, value)

        # 生成失敗は RuntimeError で扱う (abort しない)
        try:
            h3.Session.create_client(config)
        except RuntimeError:
            continue
        raise AssertionError("上限超えの値で生成失敗しませんでした")


# ========== stream_id 全域の堅牢性テスト ==========


@given(st.integers(), st.integers(), st.booleans())
@settings(max_examples=100)
def prop_open_stream_arbitrary_id_no_abort(
    session_id: int, stream_id: int, is_unidirectional: bool
):
    """整数全域の ID で open_stream しても abort しない

    確立済みセッション上で重複 ID・非自起点 ID・範囲外 ID を渡し、成功か
    False のいずれかで返ることを検証する。int64 に収まらない値は binding
    境界で TypeError になるが、いずれも abort しない
    """
    # 有効なセッションを事前確立する (異常系再現に必要)
    client, _server, established_session_id = _establish_session()

    # 確立済みセッション ID と任意の ID の両方で呼び出す。open_stream は
    # 異常系を False で返す契約のため、ValueError は投げない。int64 に収ま
    # らない値は binding 境界で TypeError になるが、いずれも abort しない
    for target_session_id in (established_session_id, session_id):
        try:
            result = client.open_stream(target_session_id, stream_id, is_unidirectional)
        except TypeError, OverflowError:
            continue
        assert result is False or result is True


@given(st.integers(), st.binary(max_size=1024), st.booleans())
@settings(max_examples=100)
def prop_receive_stream_data_arbitrary_id_no_abort(stream_id: int, data: bytes, fin: bool):
    """整数全域の stream_id で receive_stream_data しても abort しない

    負値や varint 上限 (2^62 - 1) 超えは ValueError で拒否し、有効範囲内は
    処理バイト数を返す。いずれも nghttp3 の assert に到達しない
    """
    config = h3.Config()
    session = h3.Session.create_client(config)

    # 不正 ID は ValueError、有効 ID は処理バイト数を返す。int64 に収まら
    # ない値は binding 境界で TypeError になるが、いずれも abort しない
    try:
        processed = session.receive_stream_data(stream_id, data, fin)
    except ValueError, TypeError, OverflowError:
        return
    assert processed <= len(data)


def test_set_max_client_streams_bidi_decrease_raises() -> None:
    """set_max_client_streams_bidi の減少値が ValueError になる"""
    # サーバーセッションを用意し、累積最大数を 100 に設定する
    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    session.set_max_client_streams_bidi(100)

    # 減少値は nghttp3 の assert に到達する前に ValueError で拒否される
    try:
        session.set_max_client_streams_bidi(50)
    except ValueError:
        pass
    else:
        raise AssertionError("減少値で ValueError になりませんでした")

    # 同値と増加値は受け付ける (単調増加の仕様どおり)
    session.set_max_client_streams_bidi(100)
    session.set_max_client_streams_bidi(200)


def test_set_max_client_streams_bidi_client_ignored() -> None:
    """クライアントの set_max_client_streams_bidi が黙って無視される"""
    # クライアントセッションでは nghttp3 がサーバーを assert するため、
    # バインディング側で黙って無視する (abort しない)
    config = h3.Config()
    session = h3.Session.create_client(config)

    # 黙って無視され、例外も abort も起きない
    session.set_max_client_streams_bidi(100)


def test_receive_stream_data_invalid_id_raises_value_error() -> None:
    """範囲外 ID の receive_stream_data が ValueError になる"""
    # 負値と varint 上限 (2^62 - 1) 超えは nghttp3 の assert に到達する前に
    # ValueError で拒否される
    config = h3.Config()
    session = h3.Session.create_client(config)

    # 不正 ID はいずれも ValueError になる (abort しない)
    for bad_id in (-1, 2**62):
        try:
            session.receive_stream_data(bad_id, b"data", False)
        except ValueError:
            continue
        raise AssertionError("範囲外 ID で ValueError になりませんでした")


def test_receive_stream_data_bound_uni_no_abort() -> None:
    """バインド済みの自単方向ストリームへの受信が黙って無視される"""
    # クライアントとサーバーで自起点の制御・QPACK ストリームをバインドする
    client = h3.Session.create_client(h3.Config())
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # 自単方向ストリームは送信専用のため、受信しても nghttp3 の assert に
    # 到達せず黙って無視される (abort しない)
    for stream_id in (2, 6, 10):
        assert client.receive_stream_data(stream_id, b"data", False) == 0
    for stream_id in (3, 7, 11):
        assert server.receive_stream_data(stream_id, b"data", False) == 0


# ========== エラーコードの復元 (draft-16 Section 4.4) ==========


@given(st.integers(min_value=0, max_value=0xFFFFFFFF))
@settings(max_examples=100)
def prop_deliver_stream_reset_error_code_roundtrip(app_code: int):
    """データストリームのリセットはワイヤ→アプリの復元で元の 32bit コードに戻る

    webtransport_code_to_http_code でワイヤへ写したコードを
    deliver_stream_reset_error_code で配信すると、元のアプリコードに戻る
    (draft-16 Section 4.4 Figure 4 / draft-ietf-webtrans-overview-13 の
    unsigned 32-bit 契約)。
    """
    wire = webtransport_code_to_http_code(app_code)
    assert (
        deliver_stream_reset_error_code(wire_error_code=wire, is_connect_stream=False) == app_code
    )
