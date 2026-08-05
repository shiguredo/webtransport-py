"""実ブラウザ (Chromium / WebKit) を使った WebTransport E2E テスト用フィクスチャ

pytest-playwright の同期 API から asyncio ベースの Server を扱うため、
サーバーは別スレッドで asyncio.run() により起動する。ティアダウンでは
別スレッドのイベントループに停止処理をスケジュールして停止する。

ブラウザは pytest-playwright の --browser オプションには依存せず、ファイル
単位でブラウザを固定するため、sync_playwright() で直接起動したブラウザを
フィクスチャとして提供する。
"""

import asyncio
import base64
import queue
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from playwright.sync_api import Browser, Page, sync_playwright

from webtransport.h3 import Server


class BrowserWebTransportServer:
    """実ブラウザテスト用の WebTransport echo サーバー

    別スレッドでイベントループを回し、同期 pytest から接続できるようにする。
    サーバー側のイベント (on_session_ready 等) はイベント種別ごとの共有キューに
    積み、wait_event() で観測する。
    """

    def __init__(self, certfile: str, keyfile: str, allowed_origins: list[str]) -> None:
        self._server = Server(
            host="127.0.0.1",
            port=0,
            certfile=certfile,
            keyfile=keyfile,
            allowed_origins=allowed_origins,
        )
        # イベント種別ごとの共有キュー (スレッド間通信)
        self._event_queues: dict[str, queue.Queue[tuple[Any, ...]]] = {}
        self._stop_requested = threading.Event()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def actual_port(self) -> int:
        """実際にバインドしているポート番号"""
        return self._server.actual_port

    @property
    def url(self) -> str:
        """接続先 URL (DevTools の url パラメータに渡す)"""
        return f"https://127.0.0.1:{self.actual_port}/webtransport"

    def _enqueue(self, name: str, *payload: Any) -> None:
        """イベントを種別ごとの共有キューに積む"""
        event_queue = self._event_queues.get(name)
        if event_queue is None:
            event_queue = queue.Queue()
            self._event_queues[name] = event_queue
        event_queue.put(payload)

    def wait_event(self, name: str, timeout: float = 10.0) -> tuple[Any, ...]:
        """共有キューから指定イベントを待つ

        Args:
            name: イベント名 (session_ready 等)
            timeout: 待ち時間 (秒)

        Returns:
            イベントのペイロード (コールバック引数)

        Raises:
            queue.Empty: タイムアウトした場合
        """
        event_queue = self._event_queues.get(name)
        if event_queue is None:
            event_queue = queue.Queue()
            self._event_queues[name] = event_queue
        return event_queue.get(timeout=timeout)

    def _wire_callbacks(self) -> None:
        """サーバーのコールバックを共有キューへの積み込みに接続する

        echo サーバーとして、双方向ストリーム (QUIC stream_id % 4 == 0) と
        データグラムのみエコーバックする。クライアント起点の単方向ストリーム
        (QUIC stream_id % 4 == 2) はエコーしない。
        """
        self._server.on_session_ready(self._on_session_ready)
        self._server.on_session_closed(self._on_session_closed)
        self._server.on_stream_data(self._on_stream_data)
        self._server.on_datagram(self._on_datagram)

    async def _on_session_ready(
        self,
        session_id: int,
        addr: tuple[str, int],
    ) -> None:
        self._enqueue("session_ready", session_id, addr)
        # セッション確立をトリガーにサーバーからの単方向ストリームを 1 回送信する
        # (戻り値が 0 以上であることはテスト側で確認する)
        stream_id = await self._server.open_stream(addr, session_id)
        self._enqueue("server_stream_opened", stream_id, addr)
        if stream_id >= 0:
            await self._server.send_stream_data(
                addr,
                stream_id,
                b"server-unidirectional-payload",
                fin=True,
            )
            self._enqueue("server_stream_sent", stream_id, addr)

    async def _on_session_closed(
        self,
        session_id: int,
        addr: tuple[str, int],
    ) -> None:
        self._enqueue("session_closed", session_id, addr)

    async def _on_stream_data(
        self,
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        self._enqueue("stream_data", session_id, stream_id, data, addr)
        # 双方向ストリームのみエコーバックする
        if stream_id % 4 == 0:
            await self._server.send_stream_data(addr, stream_id, data, fin=True)

    async def _on_datagram(
        self,
        session_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        self._enqueue("datagram", session_id, data, addr)
        # エコーバックする
        await self._server.send_datagram(addr, session_id, data)

    async def _monitor_stop(self) -> None:
        """停止要求を待ち、サーバーを停止する

        Server.stop() がソケットを閉じると Server.run() が OSError で
        終了する。この OSError は正常な停止経路として握りつぶす。
        """
        # threading.Event はイベントループを塞がないよう to_thread で待つ
        await asyncio.to_thread(self._stop_requested.wait)
        await self._server.stop()

    async def _main(self) -> None:
        """サーバーのメインループと停止監視を実行する"""
        await self._server.start()
        self._started.set()
        stop_task = asyncio.create_task(self._monitor_stop())
        try:
            await self._server.run()
        except OSError:
            # Server.stop() がソケットを閉じることで run() が OSError で終了する
            pass
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)

    def _run_in_thread(self) -> None:
        """別スレッドでイベントループを回す"""
        asyncio.run(self._main())

    def start(self) -> None:
        """サーバーを別スレッドで起動する"""
        self._wire_callbacks()
        self._thread = threading.Thread(target=self._run_in_thread, daemon=True)
        self._thread.start()
        # ポートバインド完了まで待つ
        if not self._started.wait(timeout=10.0):
            raise TimeoutError("サーバーの起動がタイムアウトしました")

    def stop(self) -> None:
        """サーバーを停止し、スレッドの終了を待つ"""
        self._stop_requested.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)


@pytest.fixture(scope="module")
def chromium_browser() -> Iterator[Browser]:
    """Chromium ブラウザを起動する

    テストページ (公開サイト moqt-devtools.shiguredo.app) から localhost の
    WebTransport サーバーへ接続する。Chrome の Local Network Access (LNA)
    チェックは公開サイトからローカルアドレスへの接続をブロックするため
    (net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS)、これを無効化する。
    W3C WebTransport のセキュアコンテキスト要件は自己署名証明書の
    certificateHash ピン留めで満たすため、ローカルアドレスへの接続を
    明示的に許可してもセキュリティ上の問題は生じない。
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-features=LocalNetworkAccessChecks"],
        )
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def webkit_browser() -> Iterator[Browser]:
    """WebKit (Safari) ブラウザを起動する

    WebKit には Chrome の Local Network Access (LNA) チェックに相当する
    ローカルアドレスへの接続ブロックがなく、フラグ指定は不要である。
    """
    with sync_playwright() as playwright:
        browser = playwright.webkit.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def chromium_page(chromium_browser: Browser) -> Iterator[Page]:
    """Chromium の新しいページを返す"""
    page = chromium_browser.new_page()
    yield page
    page.close()


@pytest.fixture
def webkit_page(webkit_browser: Browser) -> Iterator[Page]:
    """WebKit の新しいページを返す"""
    page = webkit_browser.new_page()
    yield page
    page.close()


def certificate_hash(certfile: str) -> str:
    """自己署名証明書の SHA-256 ハッシュを base64 で返す

    W3C WebTransport の serverCertificateHashes は証明書チェーン各証明書の
    DER 形式に対する SHA-256 ハッシュであり、DevTools の certificateHash
    パラメータはその base64 表現である。
    """
    with open(certfile, "rb") as file:
        pem = file.read()
    cert = x509.load_pem_x509_certificate(pem)
    der = cert.public_bytes(serialization.Encoding.DER)
    digest = hashes.Hash(hashes.SHA256())
    digest.update(der)
    return base64.b64encode(digest.finalize()).decode("ascii")


@pytest.fixture(scope="module")
def browser_server(test_certificates):
    """WebTransport echo サーバーを起動する

    DevTools テストページのオリジンを allowed_origins に設定する。
    ブラウザからの接続は必ず Origin ヘッダーを送るため、オリジン検証を
    通すために必要である。
    """
    server = BrowserWebTransportServer(
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        allowed_origins=["https://moqt-devtools.shiguredo.app"],
    )
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def certificate_hash_value(test_certificates) -> str:
    """テスト証明書の DER SHA-256 ハッシュ (base64) を返す"""
    return certificate_hash(test_certificates["certfile"])
