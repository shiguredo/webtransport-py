"""UDP ソケットの待機・受信とアドレス正規化

`quic` / `h3` / `http3` の Client / Server が共有する受信ヘルパー
(`wait_socket_readable` / `recv_datagram`) と、`recvfrom` / `getsockname` が
返すアドレスの正規化・送信先の決定 (`normalize_addr` /
`destination_for_packet`) を置く。
"""

import asyncio
import socket
from typing import Any, Protocol


class _Packet(Protocol):
    """送信先アドレスを持ち得るパケット (quic.Packet と同型)

    読み取り専用の property として宣言する。nanobind の公開プロパティは
    property として型付けされるため、可変属性として宣言すると実型が
    プロトコルを満たさなくなる。
    """

    @property
    def remote_host(self) -> str: ...

    @property
    def remote_port(self) -> int: ...


def normalize_addr(addr: tuple[Any, ...]) -> tuple[str, int]:
    """recvfrom / getsockname のアドレスを (str, int) に正規化する

    IPv6 の 4 要素タプル等が来ても先頭 2 要素だけを使う。port が int で
    ない場合は TypeError にする。

    Args:
        addr: recvfrom / getsockname が返したアドレス

    Returns:
        (host, port) の 2 要素タプル

    Raises:
        TypeError: port が int でない場合
    """
    host = addr[0]
    port = addr[1]
    if not isinstance(port, int):
        raise TypeError(f"expected port int, got {type(port).__name__}")
    return (str(host), port)


def destination_for_packet(
    packet: _Packet,
    remote_addr: tuple[str, int] | None,
    host: str,
    port: int,
) -> tuple[str, int]:
    """パケットの送信先アドレスを決める

    パケットにパス情報が埋まっている場合はそれを使う。無ければ解決済みの
    数値リモート、それも無ければ接続先のホスト名とポートにフォールバック
    する (C++ 側で解決されるが family 食い違いの余地が残る)。

    Args:
        packet: 送信するパケット
        remote_addr: 解決済みの数値リモートアドレス (未解決なら None)
        host: 接続先ホスト
        port: 接続先ポート

    Returns:
        (host, port) の 2 要素タプル
    """
    if packet.remote_host and packet.remote_port:
        return (packet.remote_host, packet.remote_port)
    if remote_addr is not None:
        return remote_addr
    return (host, port)


async def wait_socket_readable(sock: socket.socket, timeout: float) -> bool:
    """ソケットが読み取り可能になるまで待つ

    `asyncio.wait_for(loop.sock_recvfrom(...), timeout=...)` は macOS の
    kqueue セレクタでデータグラムを取りこぼす。`sock_recvfrom` はタイムアウト
    ごとに future をキャンセルしてセレクタ登録を解除するため、解除と再登録の
    あいだに届いたパケットの読み取り可能通知が失われ、再登録後もコールバックが
    呼ばれないまま次のパケットを待ち続ける。失われたパケットはピアの再送
    (PTO) まで届かず、ハンドシェイク中のパケットが失われると PTO のバック
    オフで数秒単位の遅延になる。

    ここでは待機専用の future を `add_reader` に登録し、タイムアウトは
    `call_later` で扱う。`wait_for` によるタスクのキャンセルに依存しない
    ため、セレクタ登録の解除はこの関数の `finally` で 1 回だけ起きる。
    `sock_recvfrom` のようにキャンセルと解除が競合しないので、タイムアウト
    の直後に届いたパケットの通知を失わない。

    Args:
        sock: 非ブロッキングの UDP ソケット
        timeout: 読み取り可能になるまで待つ上限 (秒)

    Returns:
        読み取り可能になった場合は True。timeout を過ぎた場合は False

    Raises:
        asyncio.CancelledError: 呼び出し元のタスクがキャンセルされた場合
    """
    if timeout <= 0:
        return False

    loop = asyncio.get_running_loop()
    file_descriptor = sock.fileno()
    readable: asyncio.Future[bool] = loop.create_future()

    def on_readable() -> None:
        """読み取り可能通知を future に伝える"""
        if not readable.done():
            readable.set_result(True)

    def on_timeout() -> None:
        """待機上限を過ぎたことを future に伝える"""
        if not readable.done():
            readable.set_result(False)

    loop.add_reader(file_descriptor, on_readable)
    timeout_handle = loop.call_later(timeout, on_timeout)
    try:
        return await readable
    finally:
        timeout_handle.cancel()
        loop.remove_reader(file_descriptor)


async def recv_datagram(
    sock: socket.socket,
    timeout: float,
) -> tuple[bytes, tuple[Any, ...]] | None:
    """データグラムを 1 件受信する

    待機前にソケットを直接読み、受信済みなら待たずに返す。待機中に届いた
    パケットを取りこぼさないことと、受信キューに溜まっているときに無駄な
    ウェイクアップを挟まないことを両立する。

    Args:
        sock: 非ブロッキングの UDP ソケット
        timeout: 最初の 1 件を待つ上限 (秒)

    Returns:
        (受信データ, 送信元アドレス)。timeout までに受信できなければ None。
        アドレスは `normalize_addr` に渡すため、IPv6 の 4 要素タプル等も
        あり得るので `tuple[Any, ...]` で返す

    Raises:
        asyncio.CancelledError: 呼び出し元のタスクがキャンセルされた場合
    """
    try:
        return sock.recvfrom(65535)
    except BlockingIOError, InterruptedError:
        pass

    if not await wait_socket_readable(sock, timeout):
        return None

    try:
        return sock.recvfrom(65535)
    except BlockingIOError, InterruptedError:
        # 待機中に別の処理が読み取った場合に備える
        return None
