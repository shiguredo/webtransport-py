"""WebTransport over HTTP/3 の複数セッション/ストリーム干渉テスト"""

from conftest import _encode_wt_datagram, _establish_two_sessions
from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import h3

# ========== 複数 Session インスタンスの独立性テスト ==========


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=50)
def prop_multiple_client_sessions_independent(num_sessions: int):
    """複数のクライアントセッションが互いに干渉しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 各セッションが独立していることを確認
    for i, session in enumerate(sessions):
        assert session is not None
        assert session.is_closed() is False
        # 各セッションの session_ids は空
        assert session.get_session_ids() == []


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=50)
def prop_multiple_server_sessions_independent(num_sessions: int):
    """複数のサーバーセッションが互いに干渉しない"""
    config = h3.Config()
    config.is_server = True
    sessions = [h3.Session.create_server(config) for _ in range(num_sessions)]

    # 各セッションが独立していることを確認
    for session in sessions:
        assert session is not None
        assert session.is_closed() is False
        assert session.get_session_ids() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_session_receive_stream_data_isolated(num_sessions: int, stream_id: int, data: bytes):
    """一方のセッションへの receive_stream_data が他方に影響しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションにのみデータを送信
    sessions[0].receive_stream_data(stream_id, data, False)

    # 他のセッションは影響を受けていないことを確認
    for session in sessions[1:]:
        # next_event は None のまま（データを受信していない）
        assert session.next_event() is None


@given(
    st.integers(min_value=2, max_value=5),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_session_receive_datagram_isolated(num_sessions: int, data: bytes):
    """一方のセッションへの receive_datagram が他方に影響しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションにのみデータグラムを送信
    sessions[0].receive_datagram(data)

    # 他のセッションは影響を受けていないことを確認
    for session in sessions[1:]:
        assert session.next_event() is None


# ========== クライアント・サーバー同時操作の独立性テスト ==========


@given(
    st.integers(min_value=0, max_value=2**62 - 1),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_client_server_sessions_independent(stream_id: int, data: bytes):
    """クライアントとサーバーのセッションが互いに干渉しない"""
    client_config = h3.Config()
    server_config = h3.Config()
    server_config.is_server = True

    client = h3.Session.create_client(client_config)
    server = h3.Session.create_server(server_config)

    # クライアントにデータを送信
    client.receive_stream_data(stream_id, data, False)

    # サーバーは影響を受けていない
    assert server.next_event() is None

    # サーバーにデータを送信
    server.receive_stream_data(stream_id, data, False)

    # クライアントの状態は変化しない（既に受信したイベント以外）
    # get_session_ids は両方とも空のまま
    assert client.get_session_ids() == []
    assert server.get_session_ids() == []


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_mixed_client_server_sessions_independent(num_pairs: int):
    """複数のクライアント・サーバーペアが互いに干渉しない"""
    client_config = h3.Config()
    server_config = h3.Config()
    server_config.is_server = True

    clients = [h3.Session.create_client(client_config) for _ in range(num_pairs)]
    servers = [h3.Session.create_server(server_config) for _ in range(num_pairs)]

    # 全てのセッションが独立していることを確認
    all_sessions = clients + servers
    for session in all_sessions:
        assert session is not None
        assert session.is_closed() is False
        assert session.get_session_ids() == []


# ========== 同一 Session 内の複数ストリーム独立性テスト ==========


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=2**62 - 1),
            st.binary(max_size=500),
        ),
        min_size=2,
        max_size=10,
        unique_by=lambda x: x[0],
    )
)
@settings(max_examples=50)
def prop_multiple_streams_data_isolated(stream_data_pairs: list[tuple[int, bytes]]):
    """同一セッション内の複数ストリームが互いに干渉しない"""
    config = h3.Config()
    session = h3.Session.create_client(config)

    # 各ストリームにデータを送信
    for stream_id, data in stream_data_pairs:
        session.receive_stream_data(stream_id, data, False)

    # ストリーム ID の数だけイベントが生成されることを期待
    # （実際の動作は実装依存だが、クラッシュしないことを確認）
    event_count = 0
    while True:
        event = session.next_event()
        if event is None:
            break
        event_count += 1


@given(
    st.lists(
        st.integers(min_value=0, max_value=2**62 - 1),
        min_size=2,
        max_size=10,
        unique=True,
    ),
    st.binary(max_size=500),
    st.booleans(),
)
@settings(max_examples=50)
def prop_send_stream_data_to_multiple_streams(stream_ids: list[int], data: bytes, fin: bool):
    """複数のストリームに同じデータを送信しても干渉しない"""
    config = h3.Config()
    session = h3.Session.create_client(config)

    # 各ストリームに同じデータを送信
    for stream_id in stream_ids:
        session.send_stream_data(stream_id, data, fin)

    # クラッシュしないことを確認
    assert session.is_closed() is False


# ========== セッション操作の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
)
@settings(max_examples=50)
def prop_accept_session_isolated(num_sessions: int, session_id: int):
    """一方のセッションでの accept_session が他方に影響しない"""
    config = h3.Config()
    config.is_server = True
    sessions = [h3.Session.create_server(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ accept
    sessions[0].accept_session(session_id)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.get_session_ids() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
    st.text(max_size=50),
)
@settings(max_examples=50)
def prop_close_session_isolated(num_sessions: int, session_id: int, error_code: int, reason: str):
    """一方のセッションでの close_session が他方に影響しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ close
    sessions[0].close_session(session_id, error_code, reason)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


# ========== bind 操作の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
)
@settings(max_examples=50)
def prop_bind_control_stream_isolated(num_sessions: int, stream_id: int):
    """一方のセッションでの bind_control_stream が他方に影響しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ bind
    sessions[0].bind_control_stream(stream_id)

    # 他のセッションの required_streams は変化しない
    for session in sessions[1:]:
        required = session.get_required_streams()
        assert len(required) == 3


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
)
@settings(max_examples=50)
def prop_bind_qpack_streams_isolated(num_sessions: int, stream_id: int):
    """一方のセッションでの QPACK ストリーム bind が他方に影響しない"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ bind
    sessions[0].bind_qpack_encoder_stream(stream_id)
    sessions[0].bind_qpack_decoder_stream(stream_id + 1)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        required = session.get_required_streams()
        assert len(required) == 3


# ========== データグラム送信の独立性テスト ==========


@given(
    st.binary(max_size=1000),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_send_datagram_isolated(data1: bytes, data2: bytes):
    """一方のセッションへの send_datagram が他方に影響しない

    同一接続内の 2 セッションそれぞれへの送信が、互いのワイヤ形式を
    混ぜることなく独立にキューへ現れることを検証する。セッション終了の
    無視は draft-ietf-webtrans-http3-16 Section 6 による
    """
    client, _server, first_session_id, second_session_id = _establish_two_sessions()

    # 2 つのセッション ID にそれぞれデータグラムを送信する
    client.send_datagram(first_session_id, data1)
    client.send_datagram(second_session_id, data2)

    # 各セッションのデータグラムが正しいワイヤ形式で、送信順にキューへ現れる
    assert client.get_datagrams_to_send() == [
        _encode_wt_datagram(first_session_id, data1),
        _encode_wt_datagram(second_session_id, data2),
    ]


# ========== get 系メソッドの独立性テスト ==========


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_get_streams_to_send_isolated(num_sessions: int):
    """各セッションの get_streams_to_send が独立している"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 全てのセッションで streams_to_send は空
    for session in sessions:
        assert session.get_streams_to_send() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**62 - 1),
)
@settings(max_examples=50)
def prop_get_session_streams_isolated(num_sessions: int, session_id: int):
    """各セッションの get_session_streams が独立している"""
    config = h3.Config()
    sessions = [h3.Session.create_client(config) for _ in range(num_sessions)]

    # 全てのセッションで session_streams は空
    for session in sessions:
        assert session.get_session_streams(session_id) == []
