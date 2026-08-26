"""WebTransport over HTTP/2 Sans I/O API の Property-Based Testing"""

from conftest import _connect_h2_session, _create_h2_session_pair, _drain_events
from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import h2

# uint32 の範囲 (HTTP/2 では多くのフィールドが uint32)
UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1


# ========== Config フィールドの境界値テスト ==========


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_initial_window_size(value: int):
    """initial_window_size に任意の uint32 値を設定できる"""
    config = h2.Config()
    config.initial_window_size = value
    assert config.initial_window_size == value


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_max_concurrent_streams(value: int):
    """max_concurrent_streams に任意の uint32 値を設定できる"""
    config = h2.Config()
    config.max_concurrent_streams = value
    assert config.max_concurrent_streams == value


@given(st.integers(min_value=16384, max_value=16777215))
@settings(max_examples=100)
def prop_config_max_frame_size(value: int):
    """max_frame_size に有効な範囲の値を設定できる"""
    config = h2.Config()
    config.max_frame_size = value
    assert config.max_frame_size == value


@given(st.integers(min_value=0, max_value=UINT32_MAX))
@settings(max_examples=100)
def prop_config_max_header_list_size(value: int):
    """max_header_list_size に任意の uint32 値を設定できる"""
    config = h2.Config()
    config.max_header_list_size = value
    assert config.max_header_list_size == value


@given(st.booleans())
@settings(max_examples=10)
def prop_config_is_server(value: bool):
    """is_server に任意の bool 値を設定できる"""
    config = h2.Config()
    config.is_server = value
    assert config.is_server == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_wt_initial_max_data(value: int):
    """wt_initial_max_data に任意の uint64 値を設定できる"""
    config = h2.Config()
    config.wt_initial_max_data = value
    assert config.wt_initial_max_data == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_wt_initial_max_stream_data(value: int):
    """wt_initial_max_stream_data に任意の uint64 値を設定できる"""
    config = h2.Config()
    config.wt_initial_max_stream_data = value
    assert config.wt_initial_max_stream_data == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_wt_initial_max_streams_bidi(value: int):
    """wt_initial_max_streams_bidi に任意の uint64 値を設定できる"""
    config = h2.Config()
    config.wt_initial_max_streams_bidi = value
    assert config.wt_initial_max_streams_bidi == value


@given(st.integers(min_value=0, max_value=UINT64_MAX))
@settings(max_examples=100)
def prop_config_wt_initial_max_streams_uni(value: int):
    """wt_initial_max_streams_uni に任意の uint64 値を設定できる"""
    config = h2.Config()
    config.wt_initial_max_streams_uni = value
    assert config.wt_initial_max_streams_uni == value


# ========== Session 作成の堅牢性テスト ==========


def prop_session_create_client():
    """クライアントセッションが作成できる"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    assert session is not None
    assert session.is_closed() is False
    assert session.want_write() is True


def prop_session_create_server():
    """サーバーセッションが作成できる"""
    config = h2.Config()
    config.is_server = True
    session = h2.Session.create_server(config)
    assert session is not None
    assert session.is_closed() is False


# ========== receive / send の堅牢性テスト ==========


@given(st.binary(max_size=65536))
@settings(max_examples=100)
def prop_receive_arbitrary(data: bytes):
    """任意のデータを receive してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    processed = session.receive(data)
    assert processed >= 0


def prop_send_initial():
    """初期状態で send が HTTP/2 preface + SETTINGS を返す"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    data = session.send()
    assert data is not None
    assert len(data) > 0


# ========== connect の堅牢性テスト ==========


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/:.-", max_size=200))
@settings(max_examples=100)
def prop_connect_arbitrary(url: str):
    """任意の URL で connect してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    result = session.connect(url)
    # 正しいフォーマットでない場合は -1 を返す
    assert result >= -1


# ========== accept_session / reject_session の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=100)
def prop_accept_session_arbitrary(session_id: int):
    """任意のセッション ID で accept_session してもクラッシュしない"""
    config = h2.Config()
    config.is_server = True
    session = h2.Session.create_server(config)
    session.accept_session(session_id)


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=100)
def prop_reject_session_arbitrary(session_id: int, status_code: int):
    """任意のパラメータで reject_session してもクラッシュしない

    サーバー API は 200-599 以外の status_code を ValueError にする
    (誤用パスで「SessionClosed 非発火」の設計ピンを破らせない)。
    それ以外のパラメータ (セッション未確立等) は無視される。
    """
    config = h2.Config()
    config.is_server = True
    session = h2.Session.create_server(config)
    try:
        session.reject_session(session_id, status_code)
    except ValueError:
        pass


# ========== open_stream の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**31 - 1), st.booleans())
@settings(max_examples=100)
def prop_open_stream_arbitrary(session_id: int, is_unidirectional: bool):
    """任意のセッション ID でストリームをオープンしてもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    result = session.open_stream(session_id, is_unidirectional)
    # セッションが存在しない場合は負の値を返す
    assert result < 0


# ========== send_stream_data の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.binary(max_size=65536),
    st.booleans(),
)
@settings(max_examples=100)
def prop_send_stream_data_arbitrary(session_id: int, stream_id: int, data: bytes, fin: bool):
    """任意のデータをストリームに送信してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.send_stream_data(session_id, stream_id, data, fin)


# ========== reset_stream の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=100)
def prop_reset_stream_arbitrary(session_id: int, stream_id: int, error_code: int):
    """任意のストリームをリセットしてもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.reset_stream(session_id, stream_id, error_code)


# ========== stop_sending の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=100)
def prop_stop_sending_arbitrary(session_id: int, stream_id: int, error_code: int):
    """任意のストリームに stop_sending してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.stop_sending(session_id, stream_id, error_code)


# ========== send_datagram の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**31 - 1), st.binary(max_size=1200))
@settings(max_examples=100)
def prop_send_datagram_arbitrary(session_id: int, data: bytes):
    """任意のデータグラムを送信してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.send_datagram(session_id, data)


# ========== close_session の堅牢性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
    st.text(max_size=100),
)
@settings(max_examples=100)
def prop_close_session_arbitrary(session_id: int, error_code: int, reason: str):
    """任意のセッションをクローズしてもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.close_session(session_id, error_code, reason)


# ========== drain_session の堅牢性テスト ==========


@given(st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=100)
def prop_drain_session_arbitrary(session_id: int):
    """任意のセッションを drain してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session.drain_session(session_id)


# ========== get 系メソッドの堅牢性テスト ==========


def prop_get_session_ids():
    """get_session_ids が正しく動作する"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    session_ids = session.get_session_ids()
    assert isinstance(session_ids, list)
    assert len(session_ids) == 0  # 初期状態は空


@given(st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=100)
def prop_get_stream_ids_arbitrary(session_id: int):
    """任意のセッション ID で get_stream_ids してもクラッシュしない"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    stream_ids = session.get_stream_ids(session_id)
    assert isinstance(stream_ids, list)


# ========== next_event の堅牢性テスト ==========


def prop_next_event_returns_none():
    """初期状態で next_event が None を返す"""
    config = h2.Config()
    session = h2.Session.create_client(config)
    event = session.next_event()
    assert event is None


# ========== close_session のエラーメッセージ切り詰め (draft-15 Section 6.12) ==========


@given(st.text(min_size=1025, max_size=2000))
@settings(max_examples=100)
def prop_close_session_error_message_utf8_safe(message: str):
    """任意のエラーメッセージの close_session が UTF-8 境界で切り詰められて届く

    draft-15 Section 6.12 の MUST「Senders that truncate an application-supplied
    message MUST do so at a UTF-8 character boundary」「its length MUST NOT
    exceed 1024 bytes」に従い、message の内容によらずピアへ届く Application
    Error Message が well-formed UTF-8 で 1024 バイト以下になることを検証する。
    入力はバイト長 1024 を超える文字列のみとし、切り詰めが発生する経路を
    必ず通す (st.text の分布は小さな文字列に偏るため)。受信側は 1024 バイト超・
    不正 UTF-8 を WT_ERROR セッションエラーにするため、SessionClosed が誤って
    WT_ERROR として通知されず正常に届くこと自体が最大の不変条件である。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    client.close_session(session_id, 0, message)
    wire = client.send()
    assert wire is not None
    server.receive(wire)
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    received = closed_events[0].error_message
    assert closed_events[0].error_code == 0
    # 送信したメッセージの先頭部分 (文字境界で切れた整数バイト数) と一致する
    assert message.startswith(received)
    assert len(received.encode("utf-8")) <= 1024
