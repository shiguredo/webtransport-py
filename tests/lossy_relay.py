"""UDP パケットロスを注入するテスト用リレー

クライアントとサーバーの間に挟まる 1 対 1 の UDP リレーを提供する。
転送するパケットをドロップ規則で選別できるため、ハンドシェイクの
パケットロスに対する再送の回復を e2e テストで検証できる。

リレーはクライアントから見てサーバーの代理として振る舞い、サーバーから
見てクライアントの代理として振る舞う。このためクライアントの接続先には
リレーのアドレスを渡し、リレーは 1 つの UDP ソケットで両方向を扱う。

プロダクトコードではなくテスト専用のヘルパーであり、パケットの改変や
遅延・重複の注入は行わない (ドロップのみ)。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# 受信バッファの上限。QUIC の Initial は 1200 バイト程度だが、テストで
# 大きなデータグラムを流す場合に備えて余裕を持たせる
_RECV_BUFFER_SIZE = 65535


class _RelayProtocol(asyncio.DatagramProtocol):
    """受信したデータグラムをリレー本体へ渡す asyncio プロトコル"""

    def __init__(self, relay: LossyRelay) -> None:
        self._relay = relay

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._relay._on_datagram_received(data, addr)

    def error_received(self, exc: Exception) -> None:
        # UDP ソケットの ICMP エラー (未到達など) はドロップと同じ扱いにする。
        # テストの意図はロス下での回復であり、エラーでリレーを止めない
        logger.debug("UDP error received: %s", exc)


@dataclass(frozen=True)
class LossyRelayPacket:
    """リレーを通過しようとするパケット

    Attributes:
        direction: 転送方向。クライアントからサーバーが "c2s"、サーバーから
            クライアントが "s2c"
        index: 方向ごとの 0 始まりの通し番号。再送でも受信順に増える
        data: パケットのバイト列
    """

    direction: str
    index: int
    data: bytes


class LossyRelay:
    """ドロップ規則を注入できる 1 対 1 の UDP リレー

    async with で起動・停止する。`listen_port` に 0 を渡すと空きポートが
    自動で割り当てられ、`actual_port` で実際のポートを取得できる。クライアント
    の接続先には `("127.0.0.1", relay.actual_port)` を渡す。

    クライアントのアドレスは最初にパケットを送ってきたアドレスで確定する
    (単一クライアント前提)。サーバーはリレーのアドレスをピアとして観測する。

    Usage:
        relay = LossyRelay(server_addr=("127.0.0.1", server.actual_port))
        async with relay:
            client = Client(host="127.0.0.1", port=relay.actual_port)
    """

    def __init__(
        self,
        server_addr: tuple[str, int],
        drop_rule: Callable[[LossyRelayPacket], bool] | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
    ) -> None:
        """リレーを初期化する

        Args:
            server_addr: 転送先のサーバーアドレス (host, port)
            drop_rule: パケットを受けてドロップするかどうかを返す規則。
                True を返したパケットは転送しない。None なら全通し
            listen_host: 待ち受けるホスト
            listen_port: 待ち受けるポート。0 なら自動割り当て
        """
        self._server_addr = server_addr
        self._drop_rule = drop_rule
        self._listen_host = listen_host
        self._listen_port = listen_port

        self._transport: asyncio.DatagramTransport | None = None
        # クライアントのアドレス (最初のパケットで確定するまで None)
        self._client_addr: tuple[str, int] | None = None
        # 方向ごとの通し番号
        self._indexes = {"c2s": 0, "s2c": 0}
        # 方向ごとのドロップ数 (テストの観測用)
        self._dropped = {"c2s": 0, "s2c": 0}
        self._forwarded = {"c2s": 0, "s2c": 0}

    @property
    def actual_port(self) -> int:
        """実際に待ち受けているポート

        Raises:
            RuntimeError: 起動前に参照した場合
        """
        if self._transport is None:
            raise RuntimeError("relay is not started")
        addr = self._transport.get_extra_info("sockname")
        if addr is None:
            raise RuntimeError("relay socket address is unavailable")
        return int(addr[1])

    @property
    def server_addr(self) -> tuple[str, int]:
        """転送先のサーバーアドレス"""
        return self._server_addr

    @property
    def client_addr(self) -> tuple[str, int] | None:
        """確定したクライアントアドレス (未確定なら None)"""
        return self._client_addr

    @property
    def dropped(self) -> dict[str, int]:
        """方向別のドロップ数"""
        return dict(self._dropped)

    @property
    def forwarded(self) -> dict[str, int]:
        """方向別の転送数"""
        return dict(self._forwarded)

    async def start(self) -> None:
        """リレーを起動する"""
        if self._transport is not None:
            raise RuntimeError("relay has already been started")
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _RelayProtocol(self),
            local_addr=(self._listen_host, self._listen_port),
        )
        self._transport = transport

    async def stop(self) -> None:
        """リレーを停止する"""
        if self._transport is None:
            return
        self._transport.close()
        self._transport = None
        self._client_addr = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.stop()

    def _on_datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """データグラムを受信して方向を判定し、ドロップ規則を適用して転送する"""
        if addr == self._server_addr:
            # サーバーからのパケットはクライアントへ戻す
            self._relay(data, "s2c", self._client_addr)
            return

        # サーバー以外からのパケットはクライアントからのものとみなす。
        # 最初のパケットでクライアントアドレスを確定する
        if self._client_addr != addr:
            logger.debug("Relay learned client address: %s", addr)
            self._client_addr = addr
        self._relay(data, "c2s", self._server_addr)

    def _relay(
        self,
        data: bytes,
        direction: str,
        destination: tuple[str, int] | None,
    ) -> None:
        """方向に応じたドロップ判定と転送を行う"""
        index = self._indexes[direction]
        self._indexes[direction] = index + 1

        if self._drop_rule is not None and self._drop_rule(
            LossyRelayPacket(direction=direction, index=index, data=data)
        ):
            self._dropped[direction] += 1
            logger.info("Relay dropped %s packet #%d (%d bytes)", direction, index, len(data))
            return

        # クライアントアドレスが未確定のうちはサーバーからのパケットを
        # 転送できないためドロップする (クライアントがまだ 1 度も送っていない)
        if destination is None or self._transport is None:
            self._dropped[direction] += 1
            logger.debug("Relay has no destination for %s packet #%d", direction, index)
            return

        self._forwarded[direction] += 1
        self._transport.sendto(data, destination)
