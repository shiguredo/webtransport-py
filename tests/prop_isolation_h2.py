"""WebTransport over HTTP/2 の複数セッション/ストリーム干渉テスト"""

from hypothesis import given, settings
from hypothesis import strategies as st

from webtransport import h2

# ========== 複数 Session インスタンスの独立性テスト ==========


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=50)
def prop_multiple_client_sessions_independent(num_sessions: int):
    """複数のクライアントセッションが互いに干渉しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 各セッションが独立していることを確認
    for session in sessions:
        assert session is not None
        assert session.is_closed() is False
        # 各セッションの session_ids は空
        assert session.get_session_ids() == []


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=50)
def prop_multiple_server_sessions_independent(num_sessions: int):
    """複数のサーバーセッションが互いに干渉しない"""
    config = h2.Config()
    config.is_server = True
    sessions = [h2.Session.create_server(config) for _ in range(num_sessions)]

    # 各セッションが独立していることを確認
    for session in sessions:
        assert session is not None
        assert session.is_closed() is False
        assert session.get_session_ids() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_session_receive_isolated(num_sessions: int, data: bytes):
    """一方のセッションへの receive が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションにのみデータを送信
    sessions[0].receive(data)

    # 他のセッションは影響を受けていないことを確認
    for session in sessions[1:]:
        # next_event は None のまま（データを受信していない）
        assert session.next_event() is None


# ========== クライアント・サーバー同時操作の独立性テスト ==========


@given(st.binary(max_size=1000))
@settings(max_examples=50)
def prop_client_server_sessions_independent(data: bytes):
    """クライアントとサーバーのセッションが互いに干渉しない"""
    client_config = h2.Config()
    server_config = h2.Config()
    server_config.is_server = True

    client = h2.Session.create_client(client_config)
    server = h2.Session.create_server(server_config)

    # クライアントにデータを送信
    client.receive(data)

    # サーバーは影響を受けていない
    assert server.next_event() is None

    # サーバーにデータを送信
    server.receive(data)

    # get_session_ids は両方とも空のまま
    assert client.get_session_ids() == []
    assert server.get_session_ids() == []


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_mixed_client_server_sessions_independent(num_pairs: int):
    """複数のクライアント・サーバーペアが互いに干渉しない"""
    client_config = h2.Config()
    server_config = h2.Config()
    server_config.is_server = True

    clients = [h2.Session.create_client(client_config) for _ in range(num_pairs)]
    servers = [h2.Session.create_server(server_config) for _ in range(num_pairs)]

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
            st.integers(min_value=0, max_value=2**31 - 1),
            st.integers(min_value=0, max_value=2**62 - 1),
            st.binary(max_size=500),
        ),
        min_size=2,
        max_size=10,
        unique_by=lambda x: (x[0], x[1]),
    )
)
@settings(max_examples=50)
def prop_send_stream_data_to_multiple_streams(
    stream_data_tuples: list[tuple[int, int, bytes]],
):
    """複数のストリームにデータを送信しても干渉しない"""
    config = h2.Config()
    session = h2.Session.create_client(config)

    # 各ストリームにデータを送信
    for session_id, stream_id, data in stream_data_tuples:
        session.send_stream_data(session_id, stream_id, data, False)

    # クラッシュしないことを確認
    assert session.is_closed() is False


# ========== セッション操作の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=50)
def prop_accept_session_isolated(num_sessions: int, session_id: int):
    """一方のセッションでの accept_session が他方に影響しない"""
    config = h2.Config()
    config.is_server = True
    sessions = [h2.Session.create_server(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ accept
    sessions[0].accept_session(session_id)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.get_session_ids() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
    st.text(max_size=50),
)
@settings(max_examples=50)
def prop_close_session_isolated(num_sessions: int, session_id: int, error_code: int, reason: str):
    """一方のセッションでの close_session が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ close
    sessions[0].close_session(session_id, error_code, reason)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=50)
def prop_drain_session_isolated(num_sessions: int, session_id: int):
    """一方のセッションでの drain_session が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ drain
    sessions[0].drain_session(session_id)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


# ========== ストリーム操作の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=50)
def prop_reset_stream_isolated(num_sessions: int, session_id: int, stream_id: int, error_code: int):
    """一方のセッションでの reset_stream が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ reset
    sessions[0].reset_stream(session_id, stream_id, error_code)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=2**62 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=50)
def prop_stop_sending_isolated(num_sessions: int, session_id: int, stream_id: int, error_code: int):
    """一方のセッションでの stop_sending が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ stop_sending
    sessions[0].stop_sending(session_id, stream_id, error_code)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


# ========== データグラム送信の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.binary(max_size=1000),
)
@settings(max_examples=50)
def prop_send_datagram_isolated(num_sessions: int, session_id: int, data: bytes):
    """一方のセッションでの send_datagram が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみデータグラムを送信
    sessions[0].send_datagram(session_id, data)

    # 他のセッションは影響を受けていない
    for session in sessions[1:]:
        assert session.is_closed() is False


# ========== get 系メソッドの独立性テスト ==========


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_get_session_ids_isolated(num_sessions: int):
    """各セッションの get_session_ids が独立している"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 全てのセッションで session_ids は空
    for session in sessions:
        assert session.get_session_ids() == []


@given(
    st.integers(min_value=2, max_value=5),
    st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=50)
def prop_get_stream_ids_isolated(num_sessions: int, session_id: int):
    """各セッションの get_stream_ids が独立している"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 全てのセッションで stream_ids は空
    for session in sessions:
        assert session.get_stream_ids(session_id) == []


# ========== want_write / want_read の独立性テスト ==========


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_want_write_isolated(num_sessions: int):
    """各セッションの want_write が独立している"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションの送信データを消費
    sessions[0].send()

    # 他のセッションは影響を受けていない（まだ送信データがある）
    for session in sessions[1:]:
        assert session.want_write() is True


@given(st.integers(min_value=2, max_value=5))
@settings(max_examples=50)
def prop_send_isolated(num_sessions: int):
    """各セッションの send が独立している"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 各セッションの send は独立したデータを返す
    send_results = [session.send() for session in sessions]

    # 全てのセッションが初期データ（HTTP/2 preface + SETTINGS）を返す
    for result in send_results:
        assert result is not None
        assert len(result) > 0


# ========== connect の独立性テスト ==========


@given(
    st.integers(min_value=2, max_value=5),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
)
@settings(max_examples=50)
def prop_connect_isolated(num_sessions: int, path: str):
    """一方のセッションでの connect が他方に影響しない"""
    config = h2.Config()
    sessions = [h2.Session.create_client(config) for _ in range(num_sessions)]

    # 最初のセッションでのみ connect
    url = f"https://example.com/{path}"
    sessions[0].connect(url)

    # 他のセッションの session_ids は空のまま
    for session in sessions[1:]:
        assert session.get_session_ids() == []
