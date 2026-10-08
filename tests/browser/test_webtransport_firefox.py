"""Firefox を使った WebTransport E2E テスト

実ブラウザ (Firefox) の WebTransport API から echo サーバーへの接続と送受信を
検証する。検証ロジックはヘルパー (helpers.py) にあり、ここでは Firefox ブラウザの
ページとサーバーフィクスチャを渡すだけである。

Firefox は WebTransport over HTTP/3 のみに対応しており、HTTP/2 のテスト
(test_webtransport_webkit_h2.py) は Firefox へは展開しない。
"""

import pytest
from helpers import (
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


def test_browser_e2e_webtransport(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox から WebTransport サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_send_order_stream(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox で sendOrder を指定した双方向ストリームを検証する"""
    run_browser_e2e_send_order_stream(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_with_code(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox で closeCode / reason を指定した切断を検証する"""
    run_browser_e2e_close_with_code(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_datagram_settings(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox でデータグラムの動的設定を検証する"""
    run_browser_e2e_datagram_settings(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_connection_options(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox で WebTransport オプション指定時の接続を検証する"""
    run_browser_e2e_connection_options(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_custom_headers(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox でカスタムヘッダー付きの接続を検証する"""
    run_browser_e2e_custom_headers(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_stream_options(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox でストリーム作成オプション (sendOrder / waitUntilAvailable) を検証する"""
    run_browser_e2e_stream_options(firefox_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_stream(
    firefox_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Firefox で双方向ストリームの close を検証する"""
    run_browser_e2e_close_stream(firefox_page, browser_server, certificate_hash_value)
