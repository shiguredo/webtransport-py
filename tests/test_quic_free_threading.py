"""free-threading 環境でのスレッド安全性テスト

GIL 無効環境で同一ハンドルへの同時アクセスを行い、プロセスが継続する
ことを確認する (実スレッドを使う。モックなし)。C++ バインディングは
オブジェクト単位の排他で保護するため、GIL 有効環境では自明に通過する
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    _connect_h2_session,
    _create_h2_session_pair,
    _establish_session,
    create_client_server_pair,
    perform_handshake,
)

from webtransport import http2, http3


def _hammer(duration: float, first: Callable[[], None], second: Callable[[], None]) -> None:
    """2 スレッドで 2 つの操作を並行して叩き続ける

    Args:
        duration: 継続時間 (秒)
        first: 1 つ目のスレッドが繰り返す操作
        second: 2 つ目のスレッドが繰り返す操作

    Raises:
        ワーカーの例外は呼び出し元へ再送出する (join では伝播しないため)
    """
    # 終了時刻を共有し、両スレッドが同時に開始・終了する
    stop = time.monotonic() + duration
    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    first_thread = threading.Thread(target=_repeat_until, args=(stop, first, errors, errors_lock))
    second_thread = threading.Thread(target=_repeat_until, args=(stop, second, errors, errors_lock))
    first_thread.start()
    second_thread.start()
    first_thread.join()
    second_thread.join()

    # ワーカーの例外は traceback を維持して呼び出し元へ伝播させる
    if errors:
        raise errors[0]


def _repeat_until(
    stop: float,
    operation: Callable[[], None],
    errors: list[BaseException],
    errors_lock: threading.Lock,
) -> None:
    """終了時刻まで操作を繰り返す。例外は収集して呼び出し元へ返す"""
    try:
        while time.monotonic() < stop:
            operation()
    # ワーカー落ちは全て回収する意図的選択のため、blind-except を許容する
    except BaseException as error:  # noqa: BLE001
        # 組み込み型の内部ロックに頼らず明示的に排他する
        with errors_lock:
            errors.append(error)


def test_quic_connection_threads() -> None:
    """確立済み QUIC 接続への並行アクセスで abort しない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 2 スレッドで同一接続の送受信を 5 秒間叩き続ける
    _hammer(
        5.0,
        lambda: client.send(),
        lambda: client.receive(b"\x00" * 100, CLIENT_ADDR, SERVER_ADDR),
    )

    # プロセス継続後にオブジェクトが利用できる
    assert isinstance(client.is_closed(), bool)


def test_h3_session_threads() -> None:
    """確立済み WebTransport over HTTP/3 セッションへの並行アクセスで abort しない"""
    client, _server, _session_id = _establish_session()

    # 2 スレッドで送信キューの取り出しと不正データの受信を叩き続ける
    _hammer(
        2.0,
        lambda: client.get_streams_to_send(),
        lambda: client.receive_stream_data(0, b"\x00" * 100, False),
    )

    # プロセス継続後にオブジェクトが利用できる
    assert isinstance(client.is_closed(), bool)


def test_http3_connection_threads() -> None:
    """HTTP/3 接続への並行アクセスで abort しない"""
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # 2 スレッドで送信キューの取り出しとイベント取得を叩き続ける
    _hammer(
        2.0,
        lambda: client.get_streams_to_send(),
        lambda: client.next_event(),
    )

    # プロセス継続後にオブジェクトが利用できる
    assert isinstance(client.is_closed(), bool)


def test_h2_session_threads() -> None:
    """確立済み WebTransport over HTTP/2 セッションへの並行アクセスで abort しない"""
    client, server = _create_h2_session_pair()
    _connect_h2_session(client, server)

    # 2 スレッドで送信データの取り出しと不正データの受信を叩き続ける
    _hammer(
        2.0,
        lambda: client.send(),
        lambda: client.receive(b"\x00" * 100),
    )

    # プロセス継続後にオブジェクトが利用できる
    assert isinstance(client.is_closed(), bool)


def test_http2_connection_threads() -> None:
    """HTTP/2 接続への並行アクセスで abort しない"""
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    _server = http2.Connection.create_server(server_config)

    # 2 スレッドで送信データの取り出しとイベント取得を叩き続ける
    _hammer(
        2.0,
        lambda: client.send(),
        lambda: client.next_event(),
    )

    # プロセス継続後にオブジェクトが利用できる
    assert isinstance(client.is_closed(), bool)
