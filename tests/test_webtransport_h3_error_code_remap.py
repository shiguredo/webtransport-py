"""WebTransport over HTTP/3 のアプリケーションエラーコード変換テスト

draft-ietf-webtrans-http3-16 Section 4.4 / Figure 4 の変換と配信整形を検証する。
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from webtransport.h3._error_codes import (
    deliver_stream_reset_error_code,
    http_code_to_webtransport_code,
    is_wt_application_error_code,
    webtransport_code_to_http_code,
)

# draft-16 Section 4.4 の端点
_WT_APPLICATION_ERROR_FIRST = 0x52E4A40FA8DB
_WT_APPLICATION_ERROR_LAST = 0x52E5AC983162


def test_webtransport_code_to_http_code_endpoints() -> None:
    """端点 0 と 0xffffffff が仕様の端点へ写ることを確認する"""
    assert webtransport_code_to_http_code(0) == _WT_APPLICATION_ERROR_FIRST
    assert webtransport_code_to_http_code(0xFFFFFFFF) == _WT_APPLICATION_ERROR_LAST


def test_webtransport_code_to_http_code_skips_reserved() -> None:
    """隣接アプリコードが予約済みコードポイントをまたいで連番に写ることを確認する

    floor(n / 0x1e) が増加する境界で、ワイヤコードが 1 つ飛びに増えることを
    検証する (予約済み 0x1f * N + 0x21 のスキップ)。
    """
    # n=0x1d と n=0x1e の間で floor(n/0x1e) が 0 → 1 になる
    lower = webtransport_code_to_http_code(0x1D)
    upper = webtransport_code_to_http_code(0x1E)
    assert upper == lower + 2
    # 間のコードポイントは予約済みであり、アプリコードへは写らない
    reserved = lower + 1
    assert (reserved - 0x21) % 0x1F == 0
    assert not is_wt_application_error_code(reserved)


def test_http_code_to_webtransport_code_roundtrip() -> None:
    """アプリコード → ワイヤ → アプリコードの一対一対応を確認する"""
    samples = [0, 1, 0x1D, 0x1E, 0x1F, 0x100, 0xFFFFFFFE, 0xFFFFFFFF]
    for app_code in samples:
        wire = webtransport_code_to_http_code(app_code)
        assert is_wt_application_error_code(wire)
        assert http_code_to_webtransport_code(wire) == app_code


@pytest.mark.parametrize("app_code", [0, 1, 0x1D, 0x1E, 0x42, 0xFFFFFFFF])
def test_roundtrip_parametrized(app_code: int) -> None:
    """代表値での往復変換を parametrize で確認する"""
    wire = webtransport_code_to_http_code(app_code)
    assert http_code_to_webtransport_code(wire) == app_code


def test_roundtrip_property() -> None:
    """任意の uint32 アプリコードで往復が一対一であることを hypothesis で確認する"""

    @given(st.integers(min_value=0, max_value=0xFFFFFFFF))
    def check(app_code: int) -> None:
        wire = webtransport_code_to_http_code(app_code)
        assert is_wt_application_error_code(wire)
        assert http_code_to_webtransport_code(wire) == app_code

    check()


def test_webtransport_code_to_http_code_rejects_out_of_uint32() -> None:
    """32bit 範囲超過のアプリコードを拒否することを確認する"""
    with pytest.raises(ValueError, match="uint32"):
        webtransport_code_to_http_code(-1)
    with pytest.raises(ValueError, match="uint32"):
        webtransport_code_to_http_code(0x100000000)


def test_http_code_to_webtransport_code_rejects_out_of_range() -> None:
    """レンジ外・予約済みのワイヤコードを拒否することを確認する"""
    with pytest.raises(ValueError):
        http_code_to_webtransport_code(_WT_APPLICATION_ERROR_FIRST - 1)
    with pytest.raises(ValueError):
        http_code_to_webtransport_code(_WT_APPLICATION_ERROR_LAST + 1)
    # レンジ内の予約済み
    reserved = _WT_APPLICATION_ERROR_FIRST + 1
    # first+1 が予約済みとは限らないので、確実に予約済みを探す
    for candidate in range(_WT_APPLICATION_ERROR_FIRST, _WT_APPLICATION_ERROR_FIRST + 0x40):
        if (candidate - 0x21) % 0x1F == 0:
            reserved = candidate
            break
    assert not is_wt_application_error_code(reserved)
    with pytest.raises(ValueError):
        http_code_to_webtransport_code(reserved)


def test_deliver_stream_reset_error_code_data_stream() -> None:
    """データストリームはレンジ内をそのまま、レンジ外は None にする"""
    wire = webtransport_code_to_http_code(0x42)
    assert (
        deliver_stream_reset_error_code(
            wire_error_code=wire,
            is_connect_stream=False,
        )
        == wire
    )
    assert (
        deliver_stream_reset_error_code(
            wire_error_code=0x01,
            is_connect_stream=False,
        )
        is None
    )
    assert (
        deliver_stream_reset_error_code(
            wire_error_code=0,
            is_connect_stream=False,
        )
        is None
    )


def test_deliver_stream_reset_error_code_connect_stream() -> None:
    """CONNECT ストリームはレンジ外でもワイヤコードをそのまま渡す"""
    assert (
        deliver_stream_reset_error_code(
            wire_error_code=0x42,
            is_connect_stream=True,
        )
        == 0x42
    )
    assert (
        deliver_stream_reset_error_code(
            wire_error_code=0x010E,
            is_connect_stream=True,
        )
        == 0x010E
    )


def test_session_map_send_error_code_unregistered_stream() -> None:
    """未登録ストリームもリマップし、32bit 超過は ValueError になることを確認する

    CONNECT 以外はリマップする (対向リセット後の stream_info_ 消去後も
    Section 4.4 の MUST を満たすため)。内部解放は quic.reset_stream 直呼び
    のため本 API を経由しない。
    """
    from webtransport.h3 import Config, Session

    session = Session.create_client(Config())
    assert session.map_send_error_code(4, 0x01) == webtransport_code_to_http_code(0x01)
    with pytest.raises(ValueError, match="uint32"):
        session.map_send_error_code(4, 0x100000000)


@pytest.mark.asyncio
async def test_reset_stream_rejects_out_of_uint32(test_certificates) -> None:
    """高レベル reset_stream が 32bit 超過のエラーコードを拒否することを確認する"""
    import asyncio

    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    client_addr = None
    stream_id_box: dict[str, int] = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        nonlocal client_addr
        client_addr = addr
        session_ready_event.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        stream_id_box["stream_id"] = stream_id

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    await client.connect()

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, b"x", fin=True)
        # サーバー側で stream_info_ 登録を待つ
        for _ in range(50):
            if "stream_id" in stream_id_box:
                break
            await asyncio.sleep(0.05)
        assert "stream_id" in stream_id_box
        assert client_addr is not None

        with pytest.raises(ValueError, match="uint32"):
            await server.reset_stream(
                client_addr,
                stream_id_box["stream_id"],
                error_code=0x100000000,
            )
        with pytest.raises(ValueError, match="uint32"):
            await client.reset_stream(stream_id, error_code=0x100000000)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_connect_stream_reset_is_not_remapped(test_certificates) -> None:
    """CONNECT ストリームのリセットは WT_APPLICATION_ERROR へリマップされない

    draft-ietf-webtrans-http3-16 Section 4.4 の MUST はデータストリームのみ。
    CONNECT のリセットは HTTP/3 エラーコード空間のままワイヤに載り、
    受信側 on_stream_reset にも同じ値が届くことを確認する。
    """
    import asyncio

    from webtransport.h3 import Client, Server
    from webtransport.h3._error_codes import webtransport_code_to_http_code

    session_ready_event = asyncio.Event()
    reset_received = asyncio.Event()
    client_addr = None
    client_session_id = None
    reset_info: dict[str, int | None] = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        nonlocal client_addr, client_session_id
        client_addr = addr
        client_session_id = session_id
        session_ready_event.set()

    async def on_stream_reset(
        session_id: int,
        stream_id: int,
        error_code: int | None,
        addr: tuple[str, int],
    ) -> None:
        reset_info["session_id"] = session_id
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        reset_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_reset(on_stream_reset)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    await client.connect()

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        assert client_addr is not None
        assert client_session_id is not None

        # CONNECT ストリーム (= セッション ID) をアプリが選んだ 0x03 でリセット
        await client.reset_stream(client_session_id, error_code=0x03)

        await asyncio.wait_for(reset_received.wait(), timeout=5.0)
        assert reset_info["stream_id"] == client_session_id
        assert reset_info["session_id"] == client_session_id
        # リマップされていないこと (ワイヤも配信も 0x03)
        assert reset_info["error_code"] == 0x03
        assert reset_info["error_code"] != webtransport_code_to_http_code(0x03)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_session_map_send_error_code_registered_data_stream(
    test_certificates,
) -> None:
    """登録済みデータストリームでは map_send_error_code がリマップすることを確認する"""
    import asyncio

    from webtransport.h3 import Client, Server
    from webtransport.h3._error_codes import webtransport_code_to_http_code

    session_ready_event = asyncio.Event()
    mapped: dict[str, int] = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        session_ready_event.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        client_conn = server._clients[addr]
        assert client_conn.webtransport_session is not None
        mapped["wire"] = client_conn.webtransport_session.map_send_error_code(
            stream_id,
            0x11,
        )

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    await client.connect()

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, b"x", fin=True)
        for _ in range(50):
            if "wire" in mapped:
                break
            await asyncio.sleep(0.05)
        assert mapped["wire"] == webtransport_code_to_http_code(0x11)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)
        await client.close()
        await server.stop()
