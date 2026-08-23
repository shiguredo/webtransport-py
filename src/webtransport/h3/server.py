"""WebTransport over HTTP/3 サーバー

asyncio と UDP を使用した高レベル WebTransport サーバー実装。
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Self

from webtransport import h3 as h3_low
from webtransport import quic
from webtransport.h3._transport_params import meets_transport_param_requirements

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class ClientConnection:
    """クライアント接続を表すクラス"""

    def __init__(self) -> None:
        self.quic_connection: quic.Connection | None = None
        self.webtransport_session: h3_low.Session | None = None
        self.streams_setup: bool = False


class Server:
    """WebTransport over HTTP/3 サーバー

    asyncio を使用した非同期 WebTransport サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=4433) as server:
            await server.run()

        # または
        server = Server(host="0.0.0.0", port=4433)
        await server.start()
        await server.run()
        await server.stop()
    """

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str | None = None,
        keyfile: str | None = None,
        idle_timeout_ns: int = 30_000_000_000,
        allowed_origins: list[str] | None = None,
        quic_config: quic.Config | None = None,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            allowed_origins: 許可オリジンリスト (None と空リストは
                どちらも全オリジンを受理する)
            quic_config: QUIC 設定。省略時は既定値。alpn_protocols /
                idle_timeout_ns は常に、cert_file / key_file はコンストラクタ
                引数 (certfile / keyfile) が指定された場合に接続時に上書き
                される。enable_datagram / enable_reset_stream_at を無効化
                すると WebTransport の要件を満たさないピアを作れる (テスト用)
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._idle_timeout_ns = idle_timeout_ns
        self._allowed_origins: list[str] | None = allowed_origins
        self._user_quic_config = quic_config

        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._clients: dict[tuple[str, int], ClientConnection] = {}
        self._running = False
        self._actual_port = 0

        self._on_session_ready: Callable[[int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_data: (
            Callable[[int, int, bytes, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_stream_reset: (
            Callable[[int, int, int, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_datagram: Callable[[int, bytes, tuple[str, int]], Awaitable[None]] | None = None

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

    def on_session_ready(
        self,
        callback: Callable[[int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """セッション確立時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, addr: tuple[str, int]) -> None
        """
        self._on_session_ready = callback

    def on_session_closed(
        self,
        callback: Callable[[int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """セッション終了時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, addr: tuple[str, int]) -> None
        """
        self._on_session_closed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, stream_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_stream_data = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int, int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        session_id はリセットされたストリームが属するセッション ID。
        セッション ID を復元できない場合 (WT ヘッダー未受信のまま
        リセットされたストリーム等) は -1 が渡る。

        Args:
            callback: async def callback(session_id: int, stream_id: int, error_code: int, addr: tuple[str, int]) -> None
        """
        self._on_stream_reset = callback

    def on_datagram(
        self,
        callback: Callable[[int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        session_id はデータグラムの Quarter Stream ID から復元したセッション ID
        (draft-ietf-webtrans-http3-16 Section 4.5)。不正なセッション ID (QUIC
        ストリーム ID 範囲外) のデータグラムは `on_datagram` に渡らず、接続が
        H3_ID_ERROR で閉じられる (Section 4 の MUST)。終了した・一度も確立され
        ていないセッション ID 宛のデータグラムは破棄される (実装ポリシー。
        非 2xx 拒否済みのセッションも含む)。

        Args:
            callback: async def callback(session_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_datagram = callback

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
                if client.quic_connection is None:
                    continue
                client.quic_connection.close()
                try:
                    # close() が生成した CONNECTION_CLOSE をピアへ送出する。
                    # 1 接続の送出失敗で残りの接続への送出が中断されないよう
                    # 接続ごとに例外を隔離する
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

    def _create_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> ClientConnection:
        """新しいクライアント接続を作成する

        Args:
            addr: クライアントアドレス
            initial_packet: 最初に受信したパケット
        """
        client = ClientConnection()

        quic_config = (
            self._user_quic_config if self._user_quic_config is not None else quic.Config()
        )
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            quic_config.cert_file = self._certfile
        if self._keyfile is not None:
            quic_config.key_file = self._keyfile

        webtransport_config = h3_low.Config()
        webtransport_config.is_server = True
        if self._allowed_origins is not None:
            webtransport_config.allowed_origins = self._allowed_origins

        if self._local_addr is None:
            raise RuntimeError("サーバーが開始されていません")

        client.quic_connection = quic.Connection.accept(
            quic_config,
            initial_packet,
            self._local_addr,
            addr,
        )
        client.quic_connection.receive(initial_packet, self._local_addr, addr)
        client.webtransport_session = h3_low.Session.create_server(webtransport_config)

        self._clients[addr] = client
        return client

    async def _send_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """クライアントにデータを送信する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        """
        if self._socket is None:
            return
        if client.quic_connection is None or client.webtransport_session is None:
            return

        for stream_id, stream_data, fin in client.webtransport_session.get_streams_to_send():
            client.quic_connection.send_stream_data(stream_id, stream_data, fin)

        for datagram in client.webtransport_session.get_datagrams_to_send():
            client.quic_connection.send_datagram(datagram)

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

    def _setup_streams(self, client: ClientConnection) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if client.quic_connection is None or client.webtransport_session is None:
            return
        if client.streams_setup:
            return

        control_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_control_stream(control_stream_id)

        encoder_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_qpack_encoder_stream(encoder_stream_id)

        decoder_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_qpack_decoder_stream(decoder_stream_id)

        # クライアントからの双方向ストリームを受け入れる準備
        client.webtransport_session.set_max_client_streams_bidi(100)

        client.streams_setup = True

    async def _process_quic_events(
        self,
        addr: tuple[str, int],
        client: ClientConnection,
    ) -> bool:
        """QUIC イベントを処理する

        Returns:
            接続が継続する場合は True、終了した場合は False
        """
        if client.quic_connection is None or client.webtransport_session is None:
            return False

        while True:
            quic_event = client.quic_connection.next_event()
            if quic_event is None:
                break

            if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                self._setup_streams(client)
            elif quic_event.type == quic.EventType.STREAM_DATA:
                client.webtransport_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                client.webtransport_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.STREAM_RESET:
                session_id = client.webtransport_session.close_stream(
                    quic_event.stream_id,
                    quic_event.error_code,
                )
                if self._on_stream_reset is not None:
                    await self._on_stream_reset(
                        session_id,
                        quic_event.stream_id,
                        quic_event.error_code,
                        addr,
                    )
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False

        return True

    async def _process_webtransport_events(
        self,
        addr: tuple[str, int],
        client: ClientConnection,
    ) -> None:
        """WebTransport イベントを処理する"""
        if client.webtransport_session is None:
            return

        while True:
            webtransport_event = client.webtransport_session.next_event()
            if webtransport_event is None:
                break

            if webtransport_event.type == h3_low.EventType.SESSION_READY:
                # クライアントの transport parameter を検証する
                # (draft-ietf-webtrans-http3-16 Section 3.1: クライアントは
                # max_datagram_frame_size > 0 と reset_stream_at を送ること)。
                # 要件未達なら確立済み・新規の全セッションを malformed として
                # 扱う (同 MUST)。RFC 9114 Section 4.1.2 の H3_MESSAGE_ERROR
                # をエラーコードに用いる。要件未達は接続全体のクライアント
                # transport parameter に起因し全セッションへ波及するため、
                # ストリームエラーではなく接続を閉じて扱う。評価は
                # meets_transport_param_requirements に委ねており、
                # reset_stream_at の扱い (実ブラウザ互換のため必須としない)
                # はそちらの docstring を参照する
                quic_conn = client.quic_connection
                if quic_conn is None or not meets_transport_param_requirements(quic_conn):
                    if quic_conn is not None:
                        quic_conn.close(
                            0x010E,
                            "client transport parameters do not meet WebTransport requirements",
                        )
                        # 要件未達ピアは障害・攻撃の可能性があるため、送出失敗が
                        # サーバーの run() 全体を止めないよう例外を隔離する
                        try:
                            await self._send_to(addr, client)
                        except OSError as exc:
                            logger.warning(
                                "failed to send connection close to non-compliant peer: %s",
                                exc,
                            )
                        if addr in self._clients:
                            del self._clients[addr]
                    break
                client.webtransport_session.accept_session(webtransport_event.session_id)
                if self._on_session_ready is not None:
                    await self._on_session_ready(webtransport_event.session_id, addr)

            elif webtransport_event.type == h3_low.EventType.SESSION_CLOSED:
                if self._on_session_closed is not None:
                    await self._on_session_closed(webtransport_event.session_id, addr)
                # ピアがセッションを閉じた場合、CONNECT ストリーム (セッション
                # の制御ストリーム) の送信方向を FIN で閉じてセッション終了の
                # ハンドシェイクを完了させる。応答しないとピア側はストリームの
                # クローズが完了せず、接続終了 (browser.close 等) がハングする
                if client.quic_connection is not None:
                    client.quic_connection.send_stream_data(
                        webtransport_event.session_id,
                        b"",
                        fin=True,
                    )

            elif webtransport_event.type == h3_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._on_stream_data(
                        webtransport_event.session_id,
                        webtransport_event.stream_id,
                        webtransport_event.data,
                        addr,
                    )

            elif webtransport_event.type == h3_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    # receive_datagram が Quarter Stream ID から session_id を
                    # 復元し、セッション ID を検証する
                    # (draft-ietf-webtrans-http3-16 Section 4.5 / Section 4)。
                    # 範囲外のセッション ID は接続クローズとなり、終了した・
                    # 一度も確立されていないセッション ID 宛のデータグラムは
                    # 破棄されるため、ここに到達するのは生存セッションの
                    # データグラムのみである。
                    await self._on_datagram(
                        webtransport_event.session_id,
                        webtransport_event.data,
                        addr,
                    )

            elif webtransport_event.type == h3_low.EventType.RESET_STREAM:
                if client.quic_connection is not None:
                    client.quic_connection.reset_stream(
                        webtransport_event.stream_id,
                        webtransport_event.error_code,
                    )

            elif webtransport_event.type == h3_low.EventType.STOP_SENDING:
                if client.quic_connection is not None:
                    client.quic_connection.stop_sending(
                        webtransport_event.stream_id,
                        webtransport_event.error_code,
                    )

            elif webtransport_event.type == h3_low.EventType.ERROR:
                # データグラムの不正なセッション ID 受信 (H3_ID_ERROR) のみ
                # 接続クローズを扱う。receive_stream_data が生成する nghttp3
                # エラー (error_code が 0x0108 でない) は対象外
                if webtransport_event.error_code == 0x0108 and client.quic_connection is not None:
                    client.quic_connection.close(
                        webtransport_event.error_code,
                        webtransport_event.error_message,
                    )
                    await self._send_to(addr, client)
                    # CONNECTION_CLOSE 送出後にエントリを削除し、
                    # 同一アドレスからの再接続をブロックしないようにする
                    if addr in self._clients:
                        del self._clients[addr]
                    # 同一バッチに積まれた残りのイベント (クローズ後に配送される
                    # データグラム等) の処理を打ち切る
                    break

    async def send_stream_data(
        self,
        addr: tuple[str, int],
        stream_id: int,
        data: bytes,
        fin: bool = False,
    ) -> None:
        """ストリームにデータを送信する

        stream_info_ に未登録のストリーム (セッション ID を復元できない)
        への送信と、受信済みの単方向ストリーム (クライアント起点 %4==2 /
        サーバー起点 %4==3) への送信は黙って無視される。

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        client = self._clients.get(addr)
        if client is None or client.webtransport_session is None:
            return

        client.webtransport_session.send_stream_data(stream_id, data, fin)
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
        if client.webtransport_session is not None:
            client.webtransport_session.reset_stream(stream_id, error_code)
        await self._send_to(addr, client)

    async def close_stream(
        self,
        addr: tuple[str, int],
        stream_id: int,
        error_code: int = 0,
    ) -> None:
        """ストリームを閉じる (RESET_STREAM)

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        await self.reset_stream(addr, stream_id, error_code)

    async def send_datagram(
        self,
        addr: tuple[str, int],
        session_id: int,
        data: bytes,
    ) -> None:
        """データグラムを送信する

        終了したセッション ID への送信は無視される
        (draft-ietf-webtrans-http3-16 Section 6 の MUST)。

        Args:
            addr: クライアントアドレス
            session_id: セッション ID
            data: 送信データ
        """
        client = self._clients.get(addr)
        if client is None or client.webtransport_session is None:
            return

        client.webtransport_session.send_datagram(session_id, data)
        await self._send_to(addr, client)

    async def open_stream(
        self,
        addr: tuple[str, int],
        session_id: int,
        unidirectional: bool = True,
    ) -> int:
        """サーバーから WebTransport ストリームを開く

        Args:
            addr: クライアントアドレス
            session_id: セッション ID (on_session_ready で受け取った有効な値)
            unidirectional: 単方向ストリームにするかどうか。False は未実装のため
                NotImplementedError を上げる

        Returns:
            ストリーム ID。失敗した場合は -1。返された stream_id は
            既存の send_stream_data で送信できる。ストリームは送信するまで
            クライアントに認識されない
        """
        # 単方向ストリームのみを対象とする (draft-ietf-webtrans-http3-16
        # Section 4.2)。サーバー起動の双方向ストリーム (Section 4.3) は
        # "can" であり実装義務が無いため未実装
        if not unidirectional:
            raise NotImplementedError("bidirectional streams are not implemented")

        client = self._clients.get(addr)
        if client is None or client.quic_connection is None or client.webtransport_session is None:
            return -1

        # QUIC の uni ストリームとして開く (RFC 9000 Section 2.1 Table 1 により
        # server-initiated unidirectional は stream_id % 4 == 3)
        stream_id = client.quic_connection.open_stream(False)
        if stream_id < 0:
            return -1

        # h3 側の登録に失敗した場合は開いた QUIC ストリームを閉じて -1 を返す
        if not client.webtransport_session.open_stream(session_id, stream_id, True):
            client.quic_connection.reset_stream(stream_id, 0)
            return -1

        return stream_id

    async def run(self) -> None:
        """メインループを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._socket is None or self._local_addr is None:
            raise RuntimeError("サーバーが開始されていません")

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
                        client = self._create_connection(addr, data)
                    except RuntimeError:
                        # 接続クローズ済みのアドレスからの追従パケット等、
                        # 未知アドレスからの非 Initial パケットは新しい接続を
                        # 開始できないため黙って破棄する (accept は Initial
                        # パケット以外で RuntimeError を投げる)。サーバーの
                        # run() を継続させる QUIC サーバーの標準挙動
                        continue
                else:
                    client = self._clients[addr]
                    if client.quic_connection is not None:
                        client.quic_connection.receive(data, self._local_addr, addr)

                connection_alive = await self._process_quic_events(addr, client)
                if not connection_alive:
                    # ピアからの CONNECTION_CLOSE への応答 (ngtcp2 が生成した
                    # CONNECTION_CLOSE) を送信してからエントリを削除する。
                    # 送信しないとピア側が応答待ちでハングする
                    await self._send_to(addr, client)
                    if addr in self._clients:
                        del self._clients[addr]
                    continue

                await self._process_webtransport_events(addr, client)
                await self._send_to(addr, client)

            except TimeoutError:
                pass

            for addr, client in list(self._clients.items()):
                if client.quic_connection is not None:
                    timeout = client.quic_connection.get_timeout()
                    if timeout is not None and timeout <= 0:
                        client.quic_connection.handle_timeout()
                        await self._send_to(addr, client)

            await asyncio.sleep(0.001)
