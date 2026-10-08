"""WebTransport over HTTP/3 のデータグラム配送を CPU 負荷下で検証する E2E テスト

小さなデータグラムを反復して送受信し、CPU 負荷をかけた状態でも配送が失われない
ことを確認する。負荷は子プロセスのビジーループで与える。同一プロセス内の
ビジーループでは受信ループ (asyncio) がイベントループを回せなくなり、別の
機序 (ソケット受信バッファのあふれ) を作り込んでしまうため、外部プロセスで
負荷をかける。

負荷は送受信ループの間ずっとかかる。負荷条件と反復回数は環境変数で調整できる。

- ``WT_H3_DATAGRAM_ITERATIONS``: 1 件ずつ送受信する反復回数 (既定 10)
- ``WT_H3_DATAGRAM_BURST``: 連続送出するデータグラム数 (既定 32)
- ``WT_H3_DATAGRAM_LOAD_PROCESSES``: 負荷用ビジーループのプロセス数 (既定 1)

既定では軽い負荷 (1 プロセス) で短い反復 (10 回) だけを回し、CI の実行時間と
安定性への影響を抑える。強い負荷をかけた長時間の反復 (100 回以上) は明示実行
する。CI と prek は ``--timeout=30`` を渡すため、それを超える場合は
``--timeout=0`` を明示する。

    WT_H3_DATAGRAM_ITERATIONS=200 WT_H3_DATAGRAM_LOAD_PROCESSES=4 uv run pytest \\
        tests/test_e2e_webtransport_h3_datagram_delivery.py -q --timeout=0
"""

import asyncio
import contextlib
import multiprocessing
import os
import time
from collections.abc import Iterator
from typing import Protocol

import pytest

from webtransport.h3 import Client, Server

# セッション確立を待つ上限 (秒)
SESSION_READY_TIMEOUT = 5.0

# 1 件のデータグラムの受信を待つ上限 (秒)。報告された再現手順に合わせる
RECV_TIMEOUT = 5.0

# CPU 負荷の子プロセスがビジーループへ入るまで待つ上限 (秒)。spawn と
# forkserver はインタプリタ起動と import を伴うため、待たずに本体へ進むと
# 負荷がかからないままテストが終わる
LOAD_STARTUP_TIMEOUT = 10.0

# 送受信するアプリケーションデータの長さの範囲 (報告では 12〜17 バイト)
PAYLOAD_LENGTH_MIN = 12
PAYLOAD_LENGTH_MAX = 17


class _StartSignal(Protocol):
    """子プロセスの起動完了を通知するシグナルの契約 (multiprocessing.Event)"""

    def set(self) -> None:
        """子プロセス側で起動完了を通知する"""
        ...

    def wait(self, timeout: float | None = None) -> bool:
        """親プロセス側で通知を待つ"""
        ...


def _cpu_burner(started: _StartSignal) -> None:
    """CPU 負荷用のビジーループ (子プロセスで実行する)

    Args:
        started: ビジーループへ入ったことを親プロセスへ通知するシグナル
    """
    started.set()
    while True:
        pass


def _wait_for_start(started: _StartSignal, child: multiprocessing.Process) -> bool:
    """子プロセスがビジーループへ入るのを待つ

    起動前に死んだ場合は待ち時間の満了を待たずに False を返す。

    Args:
        started: 子プロセスからの起動完了通知
        child: 起動した子プロセス

    Returns:
        ビジーループへ入った場合は True
    """
    deadline = time.monotonic() + LOAD_STARTUP_TIMEOUT
    while True:
        if started.wait(timeout=0.05):
            return True
        if not child.is_alive():
            return False
        if time.monotonic() >= deadline:
            return False


@contextlib.contextmanager
def _cpu_load(processes: int) -> Iterator[None]:
    """CPU 負荷をかける

    子プロセスの起動 (spawn / forkserver のインタプリタ起動と import) を
    待ってから本体へ進む。起動前に本体が終わると負荷がかからないまま
    テストが成功してしまう。

    Args:
        processes: ビジーループを回す子プロセスの数
    """
    children: list[multiprocessing.Process] = []
    try:
        for _ in range(processes):
            started = multiprocessing.Event()
            child = multiprocessing.Process(target=_cpu_burner, args=(started,), daemon=True)
            child.start()
            children.append(child)
            if not _wait_for_start(started, child):
                pytest.fail(
                    f"CPU 負荷の子プロセスがビジーループへ入りませんでした: "
                    f"終了コード {child.exitcode}"
                )
        yield
    finally:
        # 負荷は必ず止める。terminate で終わらない場合は kill して、
        # 後続のテストに CPU を奪われたままにしない
        for child in children:
            if child.is_alive():
                child.terminate()
        for child in children:
            child.join(timeout=5.0)
        for child in children:
            if child.is_alive():
                child.kill()
                child.join(timeout=5.0)


def _read_env_int(name: str, default: int) -> int:
    """環境変数から整数を読む

    Args:
        name: 環境変数名
        default: 未設定の場合の値

    Raises:
        pytest.fail: 値が整数でない、または 1 未満の場合
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        pytest.fail(f"{name} は整数で指定してください: {raw!r}")
    if value < 1:
        pytest.fail(f"{name} は 1 以上で指定してください: {value}")
    return value


def _payload(index: int) -> bytes:
    """インデックスに応じた長さのアプリケーションデータを作る

    長さを 12〜17 バイトで巡回させる。値もインデックスに依存するため、
    768 回を超えると同一のデータグラムが再登場する (連続送出のテストは
    集合で比較するため判定は壊れない)。
    """
    length = PAYLOAD_LENGTH_MIN + (index % (PAYLOAD_LENGTH_MAX - PAYLOAD_LENGTH_MIN + 1))
    return bytes([index % 256]) * length


class _Harness:
    """サーバー / クライアントを 1 セッションで接続した状態を保持する"""

    def __init__(self) -> None:
        self.server: Server | None = None
        self.client: Client | None = None
        self.server_task: asyncio.Task[None] | None = None
        self.client_task: asyncio.Task[None] | None = None
        self.addr: tuple[str, int] | None = None
        self.session_id: int | None = None
        self.session_ready: asyncio.Event = asyncio.Event()
        self.received: asyncio.Queue[bytes] = asyncio.Queue()


async def _stop_harness(harness: _Harness) -> None:
    """起動したサーバーとクライアントを停止する

    Args:
        harness: 停止する対象
    """
    for task in (harness.client_task, harness.server_task):
        if task is not None:
            task.cancel()
    await asyncio.gather(
        *[task for task in (harness.client_task, harness.server_task) if task is not None],
        return_exceptions=True,
    )
    if harness.client is not None:
        await harness.client.close()
    if harness.server is not None:
        await harness.server.stop()


async def _start_harness(test_certificates: dict[str, str]) -> _Harness:
    """サーバーとクライアントを起動し、セッションを確立する

    途中で失敗した場合は、起動済みのリソースを解放してから例外を再送出する。

    Args:
        test_certificates: テスト用の証明書 (certfile / keyfile)

    Returns:
        確立済みの _Harness
    """
    harness = _Harness()
    try:
        server = Server(
            host="127.0.0.1",
            port=0,
            certfile=test_certificates["certfile"],
            keyfile=test_certificates["keyfile"],
        )
        harness.server = server

        async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
            harness.addr = addr
            harness.session_id = session_id
            harness.session_ready.set()

        server.on_session_ready(on_session_ready)

        await server.start()

        async def run_server() -> None:
            try:
                await server.run()
            except asyncio.CancelledError:
                pass

        harness.server_task = asyncio.create_task(run_server())

        # close() の CONNECT ストリーム待ち (既定 3 秒) は本テストの対象では
        # ないため省略する (2 テストで 6 秒の短縮になる)
        client = Client(
            url=f"https://127.0.0.1:{server.actual_port}/webtransport",
            verify_peer=False,
            close_wait_timeout=0,
        )
        harness.client = client

        async def on_datagram(data: bytes) -> None:
            harness.received.put_nowait(data)

        client.on_datagram(on_datagram)

        await client.connect()

        async def run_client() -> None:
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        harness.client_task = asyncio.create_task(run_client())

        await asyncio.wait_for(harness.session_ready.wait(), timeout=SESSION_READY_TIMEOUT)
    except BaseException:
        await _stop_harness(harness)
        raise
    return harness


async def _send_datagram(harness: _Harness, data: bytes) -> None:
    """サーバーからデータグラムを 1 件送出する

    Args:
        harness: 確立済みのセッション
        data: アプリケーションデータ
    """
    assert harness.server is not None
    assert harness.addr is not None
    assert harness.session_id is not None
    await harness.server.send_datagram(harness.addr, harness.session_id, data)


@pytest.mark.asyncio
async def test_server_datagram_delivered_under_load(test_certificates) -> None:
    """CPU 負荷下でもサーバーからのデータグラムが 1 件ずつ届くことを確認する

    1 件送出するごとに受信を待つ。届かないデータグラムがあれば 5 秒で
    タイムアウトし、どの反復で欠落したかを表明で示す。
    """
    iterations = _read_env_int("WT_H3_DATAGRAM_ITERATIONS", 10)
    load_processes = _read_env_int("WT_H3_DATAGRAM_LOAD_PROCESSES", 1)

    harness = await _start_harness(test_certificates)
    try:
        # セッション確立後に負荷をかける (接続確立そのものは対象外)
        with _cpu_load(load_processes):
            for index in range(iterations):
                payload = _payload(index)
                await _send_datagram(harness, payload)
                try:
                    received = await asyncio.wait_for(harness.received.get(), timeout=RECV_TIMEOUT)
                except TimeoutError:
                    pytest.fail(
                        f"{RECV_TIMEOUT} 秒以内にデータグラムが届きませんでした: "
                        f"反復 {index + 1} / {iterations}"
                    )
                assert received == payload, (
                    f"受信したデータグラムが送信と一致しません: "
                    f"送信 {len(payload)} バイト、受信 {len(received)} バイト"
                )
    finally:
        await _stop_harness(harness)


@pytest.mark.asyncio
async def test_server_datagram_burst_delivered(test_certificates) -> None:
    """CPU 負荷下でもサーバーからの連続データグラムが欠落せず届くことを確認する

    1 件ずつではなく受信を待たずに連続で送出し、欠落と重複が無いことを
    確認する。到着順序は保証されないため、集合として一致することを確認する。
    """
    burst = _read_env_int("WT_H3_DATAGRAM_BURST", 32)
    load_processes = _read_env_int("WT_H3_DATAGRAM_LOAD_PROCESSES", 1)

    harness = await _start_harness(test_certificates)
    try:
        payloads = [_payload(index) for index in range(burst)]
        with _cpu_load(load_processes):
            # 受信を待たずに連続送出する
            for payload in payloads:
                await _send_datagram(harness, payload)

            # 全件が届くまで 1 件ずつ取り出す (順序は問わない)
            received: list[bytes] = []
            for index in range(burst):
                try:
                    received.append(
                        await asyncio.wait_for(harness.received.get(), timeout=RECV_TIMEOUT)
                    )
                except TimeoutError:
                    pytest.fail(
                        f"{RECV_TIMEOUT} 秒以内にデータグラムが届きませんでした: "
                        f"{index + 1} / {burst} 件目、"
                        f"送信 {burst} 件のうち受信 {len(received)} 件"
                    )

        assert sorted(received) == sorted(payloads), (
            "受信したデータグラムの集合が送信と一致しません: "
            f"送信 {len(payloads)} 件、受信 {len(received)} 件、"
            f"不足 {len(set(payloads) - set(received))} 件、"
            f"余剰 {len(set(received) - set(payloads))} 件"
        )
    finally:
        await _stop_harness(harness)
