"""WebTransport over HTTP/2 の TLS バージョン要件テスト

draft-ietf-webtrans-http2-15 Section 7 は、クライアントに TLS 1.3 以上、または
TLS 1.2 + extended master secret (EMS) の接続でのみ送信することを求め、サーバー
にどちらも満たさない接続のリクエストを malformed として扱うことを求める。
Python の ssl は EMS 交渉の有無を公開しないため、本実装は TLS 1.3 以上を必須
とし、TLS 1.2 以下を拒否する。TLS 1.3 の接続成功と、TLS 1.2 のみの端点との
接続拒否を検証する。
"""

import asyncio
import ssl

import pytest

from webtransport.exceptions import ConnectRefusedError, HandshakeFailedError
from webtransport.h2 import Client, Server


@pytest.mark.asyncio
async def test_h2_tls_1_3_connection_succeeds(test_certificates):
    """実 h2.Server + 実 h2.Client の TLS 1.3 接続が成功することを確認

    h2.Server / h2.Client は TLS 1.3 のみを許可する。既定の TLS 1.3 環境で
    通常の WebTransport セッション確立が成立することを回帰ピンとして確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    try:
        await client.connect(timeout=5.0)
        assert client.is_connected is True
        # 交渉された TLS バージョンが 1.3 であることを表明する。TLS 1.2 拒否の
        # 退行検出は拒否系テスト (クライアント側・サーバー側) が担う
        ssl_object = client._writer.get_extra_info("ssl_object")
        assert ssl_object is not None
        assert ssl_object.version() == "TLSv1.3"
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_h2_client_rejects_tls_1_2_only_server(test_certificates):
    """TLS 1.2 のみのサーバーへの h2.Client.connect が拒否されることを確認

    対向は生 asyncio サーバーで TLS 1.2 のみを許可する。TLS バージョン不一致で
    ハンドシェイクが失敗し、ConnectRefusedError または HandshakeFailedError が
    送出される。ハンドシェイクが成立してしまった場合 (退行) は接続を保持して
    HTTP/2 SETTINGS を送らないため、ConnectTimeoutError になり本テストは失敗
    する (即切断による ConnectRefusedError の偽陽性を避ける)。
    """
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.maximum_version = ssl.TLSVersion.TLSv1_2
    server_context.load_cert_chain(test_certificates["certfile"], test_certificates["keyfile"])

    writers: set[asyncio.StreamWriter] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # ハンドシェイク成立後も接続を保持し、HTTP/2 SETTINGS を送らない。
        # クライアント側の切断 (EOF) で終了する
        writers.add(writer)
        try:
            while await reader.read(65535):
                pass
        finally:
            writers.discard(writer)
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    client = Client(url=f"https://127.0.0.1:{port}/webtransport", verify_peer=False)
    try:
        with pytest.raises((ConnectRefusedError, HandshakeFailedError)):
            await client.connect(timeout=3.0)
    finally:
        await client.close()
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_h2_server_rejects_tls_1_2_client(test_certificates):
    """TLS 1.2 のみのクライアントからの接続が実 h2.Server に拒否されることを確認

    生 asyncio クライアントで TLS 1.2 のみを許可し、実 h2.Server へ接続する。
    証明書検証は無効化し、TLS バージョン不一致によるハンドシェイク失敗
    (接続リセットまたは ssl.SSLError) がクライアント側で観測されることを
    確認する。接続がハングした場合を偽陽性にしないため、pytest.raises の
    対象は ssl.SSLError と ConnectionResetError に限定する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    try:
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        client_context.maximum_version = ssl.TLSVersion.TLSv1_2
        with pytest.raises((ssl.SSLError, ConnectionResetError)):
            await asyncio.wait_for(
                asyncio.open_connection(
                    "127.0.0.1",
                    server.actual_port,
                    ssl=client_context,
                ),
                timeout=5.0,
            )
    finally:
        await server.stop()
