"""WebKit (Safari) を使った WebTransport over HTTP/2 E2E テスト

実ブラウザ (WebKit / Safari) の WebTransport API から WebTransport over HTTP/2
echo サーバーへの接続と送受信を検証する。Safari は TCP/TLS (ALPN h2) の
https:// URL へ接続すると HTTP/2 を選択するため、WebTransport over HTTP/2
サーバーフィクスチャ (browser_server_h2) を使う。

検証ロジックはヘルパー (helpers.py) にあり、ここでは WebKit ブラウザの
ページと HTTP/2 サーバーフィクスチャを渡すだけである。
"""

import pytest
from helpers import (
    assert_http2_protocol,
    run_browser_e2e_close_stream,
    run_browser_e2e_close_with_code,
    run_browser_e2e_connection_options,
    run_browser_e2e_custom_headers,
    run_browser_e2e_datagram_settings,
    run_browser_e2e_send_order_stream,
    run_browser_e2e_stream_options,
    run_browser_e2e_webtransport,
)
from playwright.sync_api import Page

pytestmark = pytest.mark.browser


def test_browser_e2e_webtransport_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) から WebTransport over HTTP/2 サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(webkit_page, browser_server_h2, certificate_hash_value)
    # HTTP/2 で接続されたことを reliability 表示で確認する
    assert_http2_protocol(webkit_page)


def test_browser_e2e_send_order_stream_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で sendOrder を指定した双方向ストリームを検証する"""
    run_browser_e2e_send_order_stream(webkit_page, browser_server_h2, certificate_hash_value)


def test_browser_e2e_close_with_code_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で closeCode / reason を指定した切断を検証する"""
    run_browser_e2e_close_with_code(webkit_page, browser_server_h2, certificate_hash_value)


def test_browser_e2e_datagram_settings_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でデータグラムの動的設定を検証する"""
    run_browser_e2e_datagram_settings(webkit_page, browser_server_h2, certificate_hash_value)


def test_browser_e2e_connection_options_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で WebTransport オプション指定時の接続を検証する

    WebTransport over HTTP/2 は requireUnreliable を指定すると HTTP/3 へ
    フォールバックが試行されるため、congestionControl のみを指定する。
    """
    run_browser_e2e_connection_options(
        webkit_page,
        browser_server_h2,
        certificate_hash_value,
        require_unreliable=False,
    )


def test_browser_e2e_custom_headers_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でカスタムヘッダー付きの接続を検証する"""
    run_browser_e2e_custom_headers(webkit_page, browser_server_h2, certificate_hash_value)


def test_browser_e2e_stream_options_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でストリーム作成オプション (sendOrder / waitUntilAvailable) を検証する"""
    run_browser_e2e_stream_options(webkit_page, browser_server_h2, certificate_hash_value)


def test_browser_e2e_close_stream_h2(
    webkit_page: Page,
    browser_server_h2,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で双方向ストリームの close を検証する"""
    run_browser_e2e_close_stream(webkit_page, browser_server_h2, certificate_hash_value)
