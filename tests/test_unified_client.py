"""統一クライアントの検証

`webtransport.Client` が `HTTPVersion` で実装を選び、プロトコル固有の API へ
`h3` / `h2` プロパティ経由で到達できることを検証する。ここでは実装の選択と
引数の検証だけを見る (実通信の検証は h3 / h2 の e2e テストが担う)。
"""

from __future__ import annotations

import pytest

from webtransport import Client, HTTPVersion, h2
from webtransport._h2_client import Client as H2ClientImpl
from webtransport._h3_client import Client as H3ClientImpl
from webtransport.exceptions import ConnectFailedError


def test_default_http_version_is_http3() -> None:
    """http_version を省略すると HTTP/3 になる"""
    client = Client(url="https://localhost:4433/webtransport")
    assert client.http_version is HTTPVersion.HTTP3
    assert isinstance(client.h3, H3ClientImpl)
    assert client.h2 is None


def test_select_http2() -> None:
    """http_version=HTTPVersion.HTTP2 で HTTP/2 の実装が選ばれる"""
    client = Client(
        url="https://localhost:4433/webtransport",
        http_version=HTTPVersion.HTTP2,
    )
    assert client.http_version is HTTPVersion.HTTP2
    assert isinstance(client.h2, H2ClientImpl)
    assert client.h3 is None
    # 実装が URL を解析してプロパティを持つ
    assert client.url == "https://localhost:4433/webtransport"
    assert client.host == "localhost"
    assert client.port == 4433
    assert client.is_connected is False
    assert client.session_id == -1


def test_http_version_values_match_alpn() -> None:
    """HTTPVersion の値が ALPN のプロトコル識別子と一致する"""
    assert HTTPVersion.HTTP2.value == "h2"
    assert HTTPVersion.HTTP3.value == "h3"


def test_http_version_accepts_value() -> None:
    """ALPN と同じ文字列からも enum を作れる"""
    client = Client(url="https://localhost:4433/webtransport", http_version="h2")
    assert client.http_version is HTTPVersion.HTTP2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ca_file": "ca.pem"},
        {"verify_callback": lambda chain: True},
        {"idle_timeout_ns": 1_000_000},
    ],
)
def test_http3_only_arguments_rejected_for_http2(kwargs: dict) -> None:
    """HTTP/3 固有の引数を HTTP/2 と組み合わせると ValueError になる"""
    with pytest.raises(ValueError, match="only available for WebTransport over HTTP/3"):
        Client(
            url="https://localhost:4433/webtransport",
            http_version=HTTPVersion.HTTP2,
            **kwargs,
        )


def test_http2_only_argument_rejected_for_http3() -> None:
    """HTTP/2 固有の引数を HTTP/3 と組み合わせると ValueError になる"""
    with pytest.raises(ValueError, match="only available for WebTransport over HTTP/2"):
        Client(
            url="https://localhost:4433/webtransport",
            http_version=HTTPVersion.HTTP3,
            config=h2.Config(),
        )


def test_common_arguments_are_forwarded() -> None:
    """共通の引数が実装へ渡る"""
    client = Client(
        url="https://localhost:4433/webtransport",
        http_version=HTTPVersion.HTTP2,
        verify_peer=False,
        origin="https://example.com",
        close_wait_timeout=1.5,
    )
    assert client.h2 is not None
    # h2 側の実装が受け取っていることを、公開されている URL 解析の結果で確認する
    assert client.h2.url == "https://localhost:4433/webtransport"


def test_callback_registration_delegates() -> None:
    """コールバック登録が実装へ委譲される"""

    async def on_datagram(data: bytes) -> None:
        pass

    client = Client(
        url="https://localhost:4433/webtransport",
        http_version=HTTPVersion.HTTP2,
    )
    client.on_datagram(on_datagram)
    assert client.h2 is not None
    assert client.h2._on_datagram is on_datagram


def test_unknown_http_version_is_rejected() -> None:
    """HTTPVersion に無い値は ValueError になる"""
    with pytest.raises(ValueError):
        Client(url="https://localhost:4433/webtransport", http_version="h4")


def test_connect_failure_raises_layer_error() -> None:
    """接続できない宛先では層の例外が送出される

    名前解決できないホスト名を使うため、実通信は発生しない。
    """
    import asyncio

    async def run() -> None:
        client = Client(
            url="https://nonexistent.invalid:443/webtransport",
            http_version=HTTPVersion.HTTP2,
        )
        try:
            await client.connect(timeout=5.0)
        finally:
            await client.close()

    with pytest.raises(ConnectFailedError):
        asyncio.run(run())
