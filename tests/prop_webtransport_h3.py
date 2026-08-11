"""WebTransport over HTTP/3 Sans I/O API の Property-Based Testing"""

from conftest import _encode_wt_datagram, _establish_session
from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import h3

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
