"""HTTP/3 サーバー

asyncio と UDP を使用した高レベル HTTP/3 サーバー実装。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import TYPE_CHECKING, Self

from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR
from webtransport.webtransport_ext import http3 as http3_low
from webtransport.webtransport_ext import quic as quic_low

logger = logging.getLogger(__name__)


def _validate_cert_key_files(certfile: str | None, keyfile: str | None) -> None:
    """証明書と鍵ファイルの存在と読み取り可能性を検証する

    None は未設定として検証を素通りする (接続時に既定動作になる)。
    起動後の削除・権限変更は run() 時の再 raise で検出する。

    Args:
        certfile: 証明書ファイルパス
        keyfile: 秘密鍵ファイルパス

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        PermissionError: ファイルが読み取り不可の場合
    """
    for name, path in (("certfile", certfile), ("keyfile", keyfile)):
        if path is None:
            continue
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{name} not found: {path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"{name} is not readable: {path}")


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
        # 直前の Error イベントで得た H3 ワイヤーエラーコードとメッセージ。
        # _close_client_connection_on_h3_error が CONNECTION_CLOSE に載せる
        self.h3_error_code: int = H3_GENERAL_PROTOCOL_ERROR
        self.h3_error_message: str = "http3 protocol error"
        # 受信 FIN が立った双方向ストリーム (on_stream_end 通知用)。
        # 受信経路とタイマー経路のどちらで QUIC イベントを取り出しても
        # 同じ通知経路になるよう接続単位で保持し、HTTP/3 層のイベントを
        # 処理したあとに通知する
        self.finished_streams: list[int] = []

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
        # DCID から接続を引く索引 (Connection Migration 後の unknown アドレス用)
        self._dcid_index: dict[bytes, ClientConnection] = {}
        # 接続ごとの索引登録済み CID (差し替えで退役分を消すため)
        self._conn_dcids: dict[ClientConnection, set[bytes]] = {}
        self._running = False
        self._actual_port = 0

        self._on_request: (
            Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_data: Callable[[int, bytes, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_end: Callable[[int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_connection_error: Callable[[int, str, tuple[str, int]], Awaitable[None]] | None = (
            None
        )

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

    def on_stream_end(
        self,
        callback: Callable[[int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """リクエストボディ終端 (FIN) 受信時のコールバックを設定する

        POST などのリクエストボディの受信完了を検知してから応答を送るために
        使う。RESET_STREAM / STOP_SENDING などで終了した場合は呼ばれない。

        Args:
            callback: async def callback(stream_id: int, addr: tuple[str, int]) -> None
        """
        self._on_stream_end = callback

    def on_connection_error(
        self,
        callback: Callable[[int, str, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """HTTP/3 プロトコルエラー検知時のコールバックを設定する

        低レベルが nghttp3 の負値 return で自主クローズしたときに、RFC 9114
        Section 8.1 の H3 ワイヤーエラーコードとメッセージが渡される。接続は
        この後 QUIC CONNECTION_CLOSE で回収される。

        Args:
            callback: async def callback(
                error_code: int,
                error_message: str,
                addr: tuple[str, int],
            ) -> None
        """
        self._on_connection_error = callback

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
        _validate_cert_key_files(self._certfile, self._keyfile)
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

    def _refresh_dcid_index(self, client: ClientConnection) -> None:
        """接続の発行済み SCID を DCID 索引に反映する

        quic.Server / h3.Server と同じ方針。8 バイト以外の CID は照会形式と
        合わないため登録しない (安全側に破棄される)。
        """
        if client not in self._clients.values():
            return
        quic_connection = client.quic_connection
        if quic_connection is None:
            return
        current = {cid for cid in quic_connection.scid if len(cid) == 8}
        previous = self._conn_dcids.get(client, set())
        for retired in previous - current:
            if self._dcid_index.get(retired) is client:
                del self._dcid_index[retired]
        for issued in current - previous:
            self._dcid_index[issued] = client
        self._conn_dcids[client] = current

    def _drop_dcid_index(self, client: ClientConnection) -> None:
        """接続の CID を索引から外す"""
        for cid in self._conn_dcids.pop(client, set()):
            if self._dcid_index.get(cid) is client:
                del self._dcid_index[cid]

    def _addr_of(self, client: ClientConnection) -> tuple[str, int] | None:
        """接続に紐づく現在のアドレスを返す (未登録なら None)"""
        for known, known_client in self._clients.items():
            if known_client is client:
                return known
        return None

    def _remove_client(self, addr: tuple[str, int]) -> None:
        """アドレスに紐づくクライアントを索引ごと取り除く"""
        client = self._clients.pop(addr, None)
        if client is not None:
            self._drop_dcid_index(client)

    async def _send_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """クライアントの送信待ちパケットを送出できるだけ送出する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        send() は輻輳ウィンドウの枯渇・フロー制御・送信待ちの解消のいずれかで
        必ず None を返すため、None まで drain しても戻ってこなくならない。
        """
        if self._socket is None:
            return
        if client.quic_connection is None or client.http3_connection is None:
            return

        for stream_id, stream_data, fin in client.http3_connection.get_streams_to_send():
            client.quic_connection.send_stream_data(stream_id, stream_data, fin)

        loop = asyncio.get_running_loop()
        while True:
            packet = client.quic_connection.send()
            if packet is None:
                return

            if packet.remote_host and packet.remote_port:
                dest: tuple[str, int] = (packet.remote_host, packet.remote_port)
            else:
                dest = addr

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
            # Error イベントを経ていればその H3 ワイヤーコード、経ていなければ
            # H3_GENERAL_PROTOCOL_ERROR を載せる
            client.quic_connection.close(client.h3_error_code, client.h3_error_message)
            await self._drain_all_to(addr, client)
        self._remove_client(addr)

    async def _drain_quic_events(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """QUIC イベントを処理する (受信経路とタイマー経路の共通処理)

        CONNECTION_CLOSED 到達では呼び出し元の addr キーで登録を外す。
        呼び出し側は quic_connection と http3_connection が非 None である
        ことを保証すること。
        """
        assert client.quic_connection is not None
        assert client.http3_connection is not None
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
                # nghttp3_conn_close_stream は残りの DATA イベントを落とす
                # ことがあるため使わない。QUIC の FIN をストリーム終端の
                # 合図として使う (高レベル Client と同じ判定)
                if quic_event.fin and quic_event.stream_id % 4 in (0, 1):
                    client.finished_streams.append(quic_event.stream_id)
            elif quic_event.type == quic_low.EventType.STREAM_RESET:
                if self._on_stream_reset is not None:
                    await self._on_stream_reset(
                        quic_event.stream_id,
                        quic_event.error_code,
                        addr,
                    )
            elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                self._remove_client(addr)
                continue

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

                client = self._clients.get(addr)
                if client is None:
                    # Connection Migration 後は送信元アドレスが変わる。
                    # Short header は DCID 索引で既存接続を引く (RFC 9000
                    # Section 5.2 に従う DCID 照合)。Long header (Initial 等)
                    # は新規 accept する
                    is_long_header = bool(data) and (data[0] & 0x80) != 0
                    if not is_long_header and len(data) >= 9:
                        candidate = self._dcid_index.get(bytes(data[1:9]))
                        if candidate is not None and candidate.quic_connection is not None:
                            result = candidate.quic_connection.receive(
                                data,
                                self._local_addr,
                                addr,
                            )
                            if result == quic_low.ReceiveResult.ACCEPTED:
                                # 正当な Migration としてアドレスキーを張り替える
                                old_addr = self._addr_of(candidate)
                                if old_addr != addr:
                                    if old_addr is not None:
                                        del self._clients[old_addr]
                                    self._clients[addr] = candidate
                                self._refresh_dcid_index(candidate)
                                client = candidate
                            elif result == quic_low.ReceiveResult.CLOSED:
                                # 移行先からの終了通知は移行先アドレスで処理する
                                client = candidate
                    if client is None:
                        if not self._running:
                            break
                        try:
                            client = self._accept_connection(addr, data)
                        except ValueError as exc:
                            # 設定不正は黙殺せず再 raise して run() を止める
                            logger.warning(
                                "failed to accept QUIC connection from %s:%d size %d first_byte %s: %s",
                                addr[0],
                                addr[1],
                                len(data),
                                data[:1].hex() if data else "none",
                                exc,
                            )
                            raise
                        except RuntimeError as exc:
                            # パケット不正のみ破棄して継続する
                            logger.warning(
                                "discarding invalid QUIC packet from %s:%d size %d first_byte %s: %s",
                                addr[0],
                                addr[1],
                                len(data),
                                data[:1].hex() if data else "none",
                                exc,
                            )
                            continue
                elif client.quic_connection is not None:
                    client.quic_connection.receive(data, self._local_addr, addr)
                    # CID ローテーションに追従するため接触のたびに最新化する
                    self._refresh_dcid_index(client)

                if client.quic_connection is None or client.http3_connection is None:
                    continue

                await self._drain_quic_events(addr, client)

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

                    # STREAM_END イベント (ヘッダー終端などで低レベルが発火する
                    # 終端検知) はここでは消費しない: on_stream_end は受信した
                    # QUIC FIN (finished_streams) の単一経路で通知する。ヘッダー
                    # と FIN が同一の QUIC STREAM_DATA として届くと両経路で
                    # 通知され 2 回呼ばれるため (RFC 9114 Section 4.1 の
                    # メッセージフレーミングと Section 6 のフレーム境界と
                    # QUIC STREAM_DATA 境界の独立性により、1 チャンクで
                    # 届くのは正当なワイヤパターン。実ブラウザ等が送り得る)。
                    # 低レベルの STREAM_END イベント (ヘッダー終端の終端検知) は
                    # 低レベル API の契約としてそのまま維持される

                    elif http3_event.type == http3_low.EventType.ERROR:
                        # HTTP/3 プロトコルエラー。CONNECTION_CLOSE に載せる
                        # error_code を後段の回収経路で使うため記録する
                        client.h3_error_code = http3_event.error_code
                        client.h3_error_message = http3_event.error_message
                        if self._on_connection_error is not None:
                            await self._on_connection_error(
                                http3_event.error_code,
                                http3_event.error_message,
                                addr,
                            )

                    elif http3_event.type == http3_low.EventType.RESET_STREAM:
                        client.quic_connection.reset_stream(
                            http3_event.stream_id,
                            http3_event.error_code,
                        )

                    elif http3_event.type == http3_low.EventType.STOP_SENDING:
                        client.quic_connection.stop_sending(
                            http3_event.stream_id,
                            http3_event.error_code,
                        )

                # HTTP/3 の DATA を処理したあとに STREAM_END を通知する。
                # コールバック未設定でも滞留させないよう必ず取り出す
                finished_streams = client.finished_streams
                client.finished_streams = []
                if self._on_stream_end is not None:
                    for stream_id in finished_streams:
                        await self._on_stream_end(stream_id, addr)

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
                        # 受信経路と同様に QUIC イベントを処理し、終了時は
                        # 登録を外す (イベント滞留によるリークを防ぐ)。
                        # タイムアウト発火で HTTP/3 層の新規イベントは生じ
                        # ないため、HTTP/3 層の drain は受信経路に委ねる
                        if client.http3_connection is not None:
                            await self._drain_quic_events(client_addr, client)
                # HTTP/3 プロトコルエラーで自主クローズした client を回収する。
                # 受信成功後分岐と対称に close 前に _send_to を通し、
                # HTTP/3 が生成した残存バイト列を吐き切ってから CONNECTION_CLOSE を送る
                if client.http3_connection is not None and client.http3_connection.is_closed():
                    await self._send_to(client_addr, client)
                    await self._close_client_connection_on_h3_error(client_addr, client)

            await asyncio.sleep(0.001)
