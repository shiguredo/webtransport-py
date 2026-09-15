"""`webtransport._common` の受信ヘルパーのテスト

高レベル API (`quic` / `h3` / `http3` の Client / Server) の受信ループが
共有する待機・受信ヘルパーを検証する。実ソケットを使い、モックは使わない。
"""

import asyncio
import socket

import pytest

from webtransport._common import recv_datagram, wait_socket_readable


def _create_udp_pair() -> tuple[socket.socket, socket.socket, tuple[str, int]]:
    """ループバックの UDP ソケットと送信先アドレスを作る

    Returns:
        (受信ソケット, 送信ソケット, 受信ソケットのアドレス)
    """
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setblocking(False)
    receiver.bind(("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return receiver, sender, receiver.getsockname()


def _close_pair(receiver: socket.socket, sender: socket.socket) -> None:
    """テストで作ったソケットを閉じる"""
    receiver.close()
    sender.close()


@pytest.mark.asyncio
async def test_wait_socket_readable_returns_true_when_data_arrives():
    """データが届いたソケットで待機が真を返すことを確認する

    受信データがある状態で `wait_socket_readable` を呼ぶと、タイムアウトを
    待たずに True を返す。
    """
    receiver, sender, address = _create_udp_pair()
    try:
        sender.sendto(b"hello", address)
        assert await wait_socket_readable(receiver, 1.0) is True
        # 待機が真を返してもデータは消費されない (呼び出し側が受信する)
        data, _ = receiver.recvfrom(65535)
        assert data == b"hello"
    finally:
        _close_pair(receiver, sender)


@pytest.mark.asyncio
async def test_wait_socket_readable_returns_false_on_timeout():
    """データが届かないソケットで待機が偽を返すことを確認する

    待機上限を過ぎると False を返し、例外は送出しない。
    """
    receiver, sender, _ = _create_udp_pair()
    try:
        assert await wait_socket_readable(receiver, 0.05) is False
    finally:
        _close_pair(receiver, sender)


@pytest.mark.asyncio
async def test_wait_socket_readable_returns_false_without_waiting_for_zero_timeout():
    """待機上限が 0 以下なら待たずに偽を返すことを確認する

    呼び出し側がタイマー期限切れを検知したときに、余分な 1 ループを
    消費しないための契約。
    """
    receiver, sender, address = _create_udp_pair()
    try:
        sender.sendto(b"hello", address)
        assert await wait_socket_readable(receiver, 0.0) is False
        assert await wait_socket_readable(receiver, -1.0) is False
    finally:
        _close_pair(receiver, sender)


@pytest.mark.asyncio
async def test_recv_datagram_returns_data_and_address():
    """データグラムと送信元アドレスを返すことを確認する"""
    receiver, sender, address = _create_udp_pair()
    try:
        sender.sendto(b"payload", address)
        result = await recv_datagram(receiver, 1.0)
        assert result is not None
        data, remote = result
        assert data == b"payload"
        # 送信元はループバックの送信ソケット。送信側の getsockname は
        # bind していないため 0.0.0.0 になるので、宛先のホストと比較する
        assert (str(remote[0]), remote[1]) == (address[0], sender.getsockname()[1])
    finally:
        _close_pair(receiver, sender)


@pytest.mark.asyncio
async def test_recv_datagram_returns_none_on_timeout():
    """データが届かない場合は None を返すことを確認する"""
    receiver, sender, _ = _create_udp_pair()
    try:
        assert await recv_datagram(receiver, 0.05) is None
    finally:
        _close_pair(receiver, sender)


@pytest.mark.asyncio
async def test_recv_datagram_does_not_lose_packets_across_timeouts():
    """タイムアウトをまたいでもパケットを取りこぼさないことを確認する

    タイムアウト付きの待機を繰り返しながらパケットを送り続け、送信した
    パケットがすべて受信できることを確認する。`asyncio.wait_for` で
    `loop.sock_recvfrom` を包む実装では、macOS の kqueue セレクタで
    タイムアウト時に読み取り可能通知が失われ、再送 (PTO) まで受信が
    止まる。ここでは待機を `add_reader` で行う `recv_datagram` が通知を
    取りこぼさないことを確かめる。
    """
    receiver, sender, address = _create_udp_pair()
    try:
        loop = asyncio.get_running_loop()
        # タイムアウトの直前にパケットが届くよう、待機上限より少し短い
        # 間隔で送る。タイムアウトとデータ到着が競合する状況を作る
        wait_timeout = 0.001
        interval = 0.0005
        packet_count = 1500

        async def send_packets() -> None:
            """一定間隔でパケットを送る"""
            for index in range(packet_count):
                await asyncio.sleep(interval)
                await loop.sock_sendto(sender, index.to_bytes(4, "big"), address)

        sender_task = asyncio.create_task(send_packets())
        received: list[bytes] = []
        deadline = loop.time() + 5.0
        try:
            while len(received) < packet_count:
                result = await recv_datagram(receiver, wait_timeout)
                if result is not None:
                    received.append(result[0])
                if loop.time() >= deadline:
                    break
        finally:
            sender_task.cancel()
            await asyncio.gather(sender_task, return_exceptions=True)

        # 送信順にすべて届く (取りこぼしも順序の入れ替わりも無い)
        assert received == [index.to_bytes(4, "big") for index in range(packet_count)]
    finally:
        _close_pair(receiver, sender)
