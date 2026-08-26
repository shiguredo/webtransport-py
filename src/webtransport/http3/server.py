"""HTTP/3 サーバー

asyncio と UDP を使用した高レベル HTTP/3 サーバー実装。
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Self

from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR
from webtransport.webtransport_ext import http3 as http3_low
from webtransport.webtransport_ext import quic as quic_low

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ClientConnection:
    """クライアント接続を表すクラス"""

    def __init__(self) -> None:
        self.quic_connection: quic_low.Connection | None = None
        self.http3_connection: http3_low.Connection | None = None
        self.control_stream_id: int = -1
        self.qpack_encoder_stream_id: int = -1
        self.qpack_decoder_stream_id: int = -1
        self.http3_streams_setup: bool = False

    def setup_http3_streams(self) -> None:
        """HTTP/3 制御ストリームとQPACKストリームを設定する"""
        if self.quic_connection is None or self.http3_connection is None:
            return
        if self.http3_streams_setup:
            return

        # サーバー側の制御ストリーム（単方向）を開く
        self.control_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.control_stream_id < 0:
            # ハンドシェイクが完了していない場合はスキップ
            return
        self.http3_connection.bind_control_stream(self.control_stream_id)

        # QPACK エンコーダーストリーム（単方向）を開く
        self.qpack_encoder_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.qpack_encoder_stream_id < 0:
            return
        self.http3_connection.bind_qpack_encoder_stream(self.qpack_encoder_stream_id)

        # QPACK デコーダーストリーム（単方向）を開く
        self.qpack_decoder_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.qpack_decoder_stream_id < 0:
            return
        self.http3_connection.bind_qpack_decoder_stream(self.qpack_decoder_stream_id)

        # クライアントからの双方向ストリームを受け入れる準備
        self.http3_connection.set_max_client_streams_bidi(100)

        self.http3_streams_setup = True


class Server:
    """HTTP/3 サーバー

    asyncio を使用した非同期 HTTP/3 サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=4433) as server:
            server.on_request(handle_request)
            await server.run()
    """

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str | None = None,
        keyfile: str | None = None,
        idle_timeout_ns: int = 30_000_000_000,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._idle_timeout_ns = idle_timeout_ns

        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._clients: dict[tuple[str, int], ClientConnection] = {}
        self._running = False
        self._actual_port = 0

        self._on_request: (
            Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_data: Callable[[int, bytes, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int, tuple[str, int]], Awaitable[None]] | None = None

    @property
    def host(self) -> str:
        """バインドしているホストアドレス"""
        return self._host

    @property
    def port(self) -> int:
        """指定されたポート番号"""
        return self._port

    @property
    def actual_port(self) -> int:
        """実際にバインドしているポート番号"""
        return self._actual_port

    @property
    def is_running(self) -> bool:
        """サーバーが実行中かどうか"""
        return self._running

    def on_request(
        self,
        callback: Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[None]],
    ) -> None:
        """リクエスト受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]) -> None
        """
        self._on_request = callback

    def on_data(
        self,
        callback: Callable[[int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_data = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int, addr: tuple[str, int]) -> None
        """
        self._on_stream_reset = callback

    def _normalize_addr(self, addr: tuple[object, ...]) -> tuple[str, int]:
        """recvfrom / getsockname のアドレスを (str, int) に正規化する"""
        host = addr[0]
        port = addr[1]
        if not isinstance(port, int):
            raise TypeError(f"expected port int, got {type(port).__name__}")
        return (str(host), port)

    async def start(self) -> None:
        """サーバーを開始する"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind((self._host, self._port))
        self._local_addr = self._normalize_addr(self._socket.getsockname())
        self._actual_port = self._local_addr[1]
        self._running = True

    async def stop(self) -> None:
        """サーバーを停止する"""
        self._running = False
        try:
            for addr, client in list(self._clients.items()):
                if client.quic_connection is not None:
                    client.quic_connection.close()
                    try:
                        # close() が生成した CONNECTION_CLOSE をピアへ
                        # 送出する。1 接続の送出失敗で残りの接続への送出が
                        # 中断されないよう接続ごとに例外を隔離する
                        # (quic / h3 層の Server.stop と同じ挙動。
                        # http3 / http3_connection のクライアント層と対称)
                        await self._send_to(addr, client)
                    except OSError as exc:
                        logger.warning("failed to send connection close: %s", exc)
        finally:
            self._clients.clear()
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.stop()

    def _accept_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> ClientConnection:
        """初期パケットから新しいクライアント接続を作成する"""
        client = ClientConnection()

        quic_config = quic_low.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            quic_config.cert_file = self._certfile
        if self._keyfile is not None:
            quic_config.key_file = self._keyfile

        http3_config = http3_low.Config()
        http3_config.is_server = True

        if self._local_addr is None:
            raise RuntimeError("server is not started")

        client.quic_connection = quic_low.Connection.accept(
            quic_config,
            initial_packet,
            self._local_addr,
            addr,
        )
        client.quic_connection.receive(initial_packet, self._local_addr, addr)
        client.http3_connection = http3_low.Connection.create_server(http3_config)

        self._clients[addr] = client
        return client

    async def _send_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """クライアントにデータを送信する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        """
        if self._socket is None:
            return
        if client.quic_connection is None or client.http3_connection is None:
            return

        for stream_id, stream_data, fin in client.http3_connection.get_streams_to_send():
            client.quic_connection.send_stream_data(stream_id, stream_data, fin)

        # send() の連続 drain は ACK 待ちが必要なケースでハングするため 1 パケットに留める
        packet = client.quic_connection.send()
        if packet is None:
            return

        if packet.remote_host and packet.remote_port:
            dest: tuple[str, int] = (packet.remote_host, packet.remote_port)
        else:
            dest = addr

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(self._socket, packet.data, dest)

    async def _drain_all_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """該当クライアントの送信キューにあるパケットをすべて送出する

        close() 後は draining 状態に入るため、_send_to の 1 パケット制約
        (ACK 待ちハング懸念) は該当しない。CONNECTION_CLOSE を含む
        残存パケットを確実にピアへ送出するために使用する。
        低レベル QUIC 側の実装バグで send() が延々と返し続けても
        run() 全体が凍らないよう防御的に上限を設ける。
        """
        if self._socket is None or client.quic_connection is None:
            return
        loop = asyncio.get_running_loop()
        # 通常は 1〜数パケットで返り値が None になる。64 は防御的な上限
        for _ in range(64):
            packet = client.quic_connection.send()
            if packet is None:
                return
            if packet.remote_host and packet.remote_port:
                dest: tuple[str, int] = (packet.remote_host, packet.remote_port)
            else:
                dest = addr
            await loop.sock_sendto(self._socket, packet.data, dest)

    async def _close_client_connection_on_h3_error(
        self, addr: tuple[str, int], client: ClientConnection
    ) -> None:
        """HTTP/3 プロトコルエラーで閉じた ClientConnection を回収する

        client.http3_connection.is_closed() が True になったときに呼び、
        QUIC 層に CONNECTION_CLOSE を送出したうえで self._clients から削除する。
        既存の CONNECTION_CLOSED ハンドラと同じ in ガード付き削除を行う。
        呼び出し側は先に _send_to を通して HTTP/3 が生成した残存バイト列を
        吐き切ってから本メソッドを呼ぶこと (受信成功後分岐・タイマー分岐の
        両方で対称)。
        """
        if client.quic_connection is not None and not client.quic_connection.is_closed():
            client.quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")
            await self._drain_all_to(addr, client)
        if addr in self._clients:
            del self._clients[addr]

    async def submit_response(
        self,
        addr: tuple[str, int],
        stream_id: int,
        headers: list[tuple[str, str]],
    ) -> None:
        """レスポンスヘッダーを送信する

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            headers: レスポンスヘッダー
        """
        client = self._clients.get(addr)
        if client is None or client.http3_connection is None:
            return

        client.http3_connection.submit_response(stream_id, headers)
        await self._send_to(addr, client)

    async def send_data(
        self,
        addr: tuple[str, int],
        stream_id: int,
        data: bytes,
        fin: bool = False,
    ) -> None:
        """ストリームにデータを送信する

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        client = self._clients.get(addr)
        if client is None or client.http3_connection is None:
            return

        client.http3_connection.send_data(stream_id, data, fin)
        await self._send_to(addr, client)

    async def reset_stream(
        self,
        addr: tuple[str, int],
        stream_id: int,
        error_code: int = 0,
    ) -> None:
        """ストリームをリセットする (QUIC RESET_STREAM + nghttp3 通知)

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        client = self._clients.get(addr)
        if client is None:
            return

        if client.quic_connection is not None:
            client.quic_connection.reset_stream(stream_id, error_code)
        if client.http3_connection is not None:
            client.http3_connection.reset_stream(stream_id, error_code)
        await self._send_to(addr, client)

    async def run(self) -> None:
        """メインループを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._socket is None or self._local_addr is None:
            raise RuntimeError("server is not started")

        loop = asyncio.get_running_loop()

        while self._running:
            try:
                data, raw_addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self._socket, 65535),
                    timeout=0.1,
                )
                addr = self._normalize_addr(raw_addr)

                if addr not in self._clients:
                    try:
                        client = self._accept_connection(addr, data)
                    except RuntimeError:
                        # 接続クローズ済みのアドレスからの追従パケット等、
                        # 未知アドレスからの非 Initial パケットは新しい
                        # 接続を開始できないため黙って破棄する (accept は
                        # Initial パケット以外で RuntimeError を投げる)。
                        # quic / h3 層の Server.run と同じ挙動 (サーバーの
                        # run() を継続させる)
                        continue
                else:
                    client = self._clients[addr]
                    if client.quic_connection is not None:
                        client.quic_connection.receive(data, self._local_addr, addr)

                if client.quic_connection is None or client.http3_connection is None:
                    continue

                while True:
                    quic_event = client.quic_connection.next_event()
                    if quic_event is None:
                        break

                    if quic_event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                        # ハンドシェイク完了時に HTTP/3 ストリームを設定する
                        # (クライアントからの PRIORITY_UPDATE を最初の
                        # フライトで受信できるように、ストリームデータの
                        # 処理より前に呼ぶ)
                        client.setup_http3_streams()
                    elif quic_event.type == quic_low.EventType.STREAM_DATA:
                        client.http3_connection.receive_stream_data(
                            quic_event.stream_id,
                            quic_event.data,
                            quic_event.fin,
                        )
                    elif quic_event.type == quic_low.EventType.STREAM_RESET:
                        if self._on_stream_reset is not None:
                            await self._on_stream_reset(
                                quic_event.stream_id,
                                quic_event.error_code,
                                addr,
                            )
                    elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                        if addr in self._clients:
                            del self._clients[addr]
                        continue

                while True:
                    http3_event = client.http3_connection.next_event()
                    if http3_event is None:
                        break

                    if http3_event.type == http3_low.EventType.HEADERS:
                        if self._on_request is not None:
                            await self._on_request(
                                http3_event.stream_id,
                                http3_event.headers,
                                addr,
                            )

                    elif http3_event.type == http3_low.EventType.DATA:
                        if self._on_data is not None:
                            await self._on_data(
                                http3_event.stream_id,
                                http3_event.data,
                                addr,
                            )

                    elif http3_event.type in (
                        http3_low.EventType.RESET_STREAM,
                        http3_low.EventType.RESET,
                    ):
                        client.quic_connection.reset_stream(
                            http3_event.stream_id,
                            http3_event.error_code,
                        )

                    elif http3_event.type == http3_low.EventType.STOP_SENDING:
                        client.quic_connection.stop_sending(
                            http3_event.stream_id,
                            http3_event.error_code,
                        )

                await self._send_to(addr, client)

                # 受信成功後の分岐: HTTP/3 プロトコルエラーで低レベルが
                # 自主クローズしていたら CONNECTION_CLOSE を送出して回収する
                if client.http3_connection is not None and client.http3_connection.is_closed():
                    await self._close_client_connection_on_h3_error(addr, client)

            except TimeoutError:
                pass

            # タイムアウト分岐でも通る per-client タイマー処理ループ。
            # ピアが黙り込んで受信成功後分岐に入らないケースを回収する。
            # ループ変数は try 節の addr とシャドウさせないため client_addr を使う
            for client_addr, client in list(self._clients.items()):
                if client.quic_connection is not None:
                    timeout = client.quic_connection.get_timeout()
                    if timeout is not None and timeout <= 0:
                        client.quic_connection.handle_timeout()
                        await self._send_to(client_addr, client)
                # HTTP/3 プロトコルエラーで自主クローズした client を回収する。
                # 受信成功後分岐と対称に close 前に _send_to を通し、
                # HTTP/3 が生成した残存バイト列を吐き切ってから CONNECTION_CLOSE を送る
                if client.http3_connection is not None and client.http3_connection.is_closed():
                    await self._send_to(client_addr, client)
                    await self._close_client_connection_on_h3_error(client_addr, client)

            await asyncio.sleep(0.001)
