"""UDP 系サーバー 3 種の設定エラーと不正パケット処理のテスト

証明書パスの誤設定を黙殺せず fail-fast することと、不正パケットの破棄継続を
検証する。実サーバーと実 UDP ソケットのみを使い、モックは使わない。
"""

from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from webtransport import h3 as h3_high
from webtransport import http3 as http3_high
from webtransport import quic as quic_high
from webtransport.webtransport_ext import quic as quic_low


def _make_server(kind: str, certfile: str, keyfile: str):
    """種別に応じたサーバーを生成する"""
    if kind == "quic":
        return quic_high.Server(host="127.0.0.1", port=0, certfile=certfile, keyfile=keyfile)
    if kind == "h3":
        return h3_high.Server(host="127.0.0.1", port=0, certfile=certfile, keyfile=keyfile)
    return http3_high.Server(host="127.0.0.1", port=0, certfile=certfile, keyfile=keyfile)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["quic", "h3", "http3"], ids=["quic", "h3", "http3"])
async def test_start_with_missing_cert_raises(kind: str, test_certificates) -> None:
    """
    存在しない証明書で start すると FileNotFoundError になることを確認する

    3 Server 共通の fail-fast である。keyfile は有効なまま certfile のみ
    不正にしても検出できる。
    """
    # 存在しないパスを渡す
    server = _make_server(kind, "/nonexistent/cert.pem", test_certificates["keyfile"])
    # start で即座に失敗する
    with pytest.raises(FileNotFoundError):
        await server.start()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["quic", "h3", "http3"], ids=["quic", "h3", "http3"])
async def test_start_with_missing_key_raises(kind: str, test_certificates) -> None:
    """
    存在しない鍵で start すると FileNotFoundError になることを確認する

    certfile は有効なまま keyfile のみ不正にする変種である。
    """
    # 存在しない鍵パスを渡す
    server = _make_server(kind, test_certificates["certfile"], "/nonexistent/key.pem")
    # start で即座に失敗する
    with pytest.raises(FileNotFoundError):
        await server.start()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["quic", "h3", "http3"], ids=["quic", "h3", "http3"])
async def test_invalid_packet_discarded_with_warning(kind: str, test_certificates, caplog) -> None:
    """
    不正パケットは破棄され run が継続し WARNING が出ることを確認する

    有効な設定で起動し、Initial 以外の短いパケットを送っても接続は増えず、
    サーバーは動き続ける。ログにアドレス・サイズ・先頭バイトが含まれる。
    """
    server = _make_server(kind, test_certificates["certfile"], test_certificates["keyfile"])
    await server.start()
    try:
        # サーバーを実行する
        server_task = asyncio.create_task(server.run())
        await asyncio.sleep(0.05)
        # 不正パケットを送る (短い非 Initial)
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            raw.sendto(b"\x00" * 10, ("127.0.0.1", server.actual_port))
        finally:
            raw.close()
        await asyncio.sleep(0.3)
        # サーバーは継続している
        assert not server_task.done()
        # WARNING が出ている
        warnings = [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "discarding invalid QUIC packet" in record.message
        ]
        assert warnings, "破棄の WARNING が出ること"
        # アドレス・サイズ・先頭バイトが含まれる
        assert "00" in warnings[0].message
    finally:
        server._running = False
        await asyncio.sleep(0.05)
        if not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["quic", "h3", "http3"], ids=["quic", "h3", "http3"])
async def test_run_reraises_config_error_with_warning(kind: str, test_certificates, caplog) -> None:
    """
    起動後に設定が壊れると run が ValueError で止まり WARNING が出ることを確認する

    有効な設定で起動後に certfile を不正パスへ差し替え、正規 Initial を送ると
    C++ 側の ValueError が再 raise される (第二関門)。3 Server 共通の分岐を
    パラメトライズで検証する。
    """
    server = _make_server(kind, test_certificates["certfile"], test_certificates["keyfile"])
    await server.start()
    # 起動後に証明書パスを壊す (ファイル削除・権限変更の模擬)
    server._certfile = "/nonexistent/cert.pem"
    try:
        # 正規 Initial を生成する
        client_config = quic_low.Config()
        client_config.alpn_protocols = ["h3"]
        client_config.verify_peer = False
        client_config.server_name = "localhost"
        client = quic_low.Connection.create_client(
            client_config, ("127.0.0.1", 50001), ("127.0.0.1", server.actual_port)
        )
        packet = client.send()
        assert packet is not None
        initial = packet.data
        # サーバーを実行する
        server_task = asyncio.create_task(server.run())
        await asyncio.sleep(0.05)
        # 正規 Initial を送る
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            raw.sendto(initial, ("127.0.0.1", server.actual_port))
        finally:
            raw.close()
        # ValueError で終了する
        with pytest.raises(ValueError):
            await asyncio.wait_for(asyncio.shield(server_task), timeout=2.0)
        # WARNING が出ている
        warnings = [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "failed to accept QUIC connection" in record.message
        ]
        assert warnings, "再 raise 時の WARNING が出ること"
    finally:
        server._running = False
        await asyncio.sleep(0.05)
        if not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["quic", "h3", "http3"], ids=["quic", "h3", "http3"])
async def test_start_with_directory_cert_raises(kind: str, tmp_path) -> None:
    """
    証明書にディレクトリを渡すと FileNotFoundError になることを確認する

    isfile 検査によりディレクトリは fail-fast する (第二関門への遅延なし)。
    """
    # ディレクトリを証明書パスとして渡す
    server = _make_server(kind, str(tmp_path), str(tmp_path))
    # start で即座に失敗する
    with pytest.raises(FileNotFoundError):
        await server.start()
