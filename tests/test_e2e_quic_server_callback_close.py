"""QUIC サーバーのアプリコールバック内から接続を閉じるテスト

`quic.Server.close(addr, error_code, reason)` は公開 API であり、プロトコル
違反を検知したアプリ (MOQT のサーバー役など) がコールバックの中から呼ぶ。
ローカル close 後の回収 (`_discard_connection`) が接続タスクを cancel すると
実行中のコールバックへ `CancelledError` が注入されるため、回収を次の走査へ
見送ることを検証する。実 UDP ソケットで通信し、モックは使わない。
"""

import asyncio
from collections.abc import Callable

import pytest

from webtransport.quic import Client, Server

# 状態変化を待つポーリングの試行上限と間隔 (秒)
WAIT_ATTEMPTS = 100
WAIT_INTERVAL = 0.05


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)"""
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


async def _wait_until(done: Callable[[], bool], message: str) -> None:
    """条件が満たされるまで上限付きで待つ

    Args:
        done: 条件を判定する述語
        message: 満たされなかった場合のエラーメッセージ (日本語)
    """
    for _ in range(WAIT_ATTEMPTS):
        if done():
            return
        await asyncio.sleep(WAIT_INTERVAL)
    raise AssertionError(message)


@pytest.mark.asyncio
async def test_close_from_callback_does_not_cancel_callback(test_certificates) -> None:
    """ストリーム受信コールバック内からの close がコールバックを中断しないことを確認する

    コールバックが close の後も処理を続けられること (CancelledError が
    注入されないこと) と、閉じた接続の登録・接続タスクが回収されることを
    確認する。回収はコールバックの実行中は見送られ、戻った後の走査で行われる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_addrs: list[tuple[str, int]] = []
    callback_finished = asyncio.Event()
    steps: list[str] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)

    async def on_stream_data(
        stream_id: int,
        data: bytes,
        fin: bool,
        addr: tuple[str, int],
    ) -> None:
        steps.append("start")
        # プロトコル違反を検知したサーバー役と同じ形で、コールバックの中から
        # 終了コードと理由付きで接続を閉じる
        await server.close(addr, 7, "protocol violation")
        steps.append("closed")
        # 回収経路が走る隙を作る。見送りが無い場合はここで CancelledError が
        # 注入され、"finished" に到達しない
        for _ in range(20):
            await asyncio.sleep(0.01)
        steps.append("finished")
        callback_finished.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await _wait_until(
            lambda: bool(server_addrs),
            "サーバー側のハンドシェイクが完了しませんでした",
        )

        stream_id = await client.open_stream(True)
        await client.send_stream_data(stream_id, b"payload")

        # コールバックが最後まで実行される (中断されない)
        await asyncio.wait_for(callback_finished.wait(), timeout=5.0)
        assert steps == ["start", "closed", "finished"]

        # 閉じた接続の登録と接続タスクは回収される (リークしない)
        await _wait_until(
            lambda: not server._connections,
            "閉じた接続の登録が回収されませんでした",
        )
        await _wait_until(
            lambda: not server._connection_tasks,
            "閉じた接続のタスクが回収されませんでした",
        )
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()
