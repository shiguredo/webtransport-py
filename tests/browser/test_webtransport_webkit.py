"""WebKit (Safari) を使った WebTransport E2E テスト

実ブラウザ (WebKit / Safari) の WebTransport API から echo サーバーへの
接続と送受信を検証する。検証ロジックはヘルパー (helpers.py) にあり、ここでは
WebKit ブラウザのページとサーバーフィクスチャを渡すだけである。
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
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) から WebTransport サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_send_order_stream(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で sendOrder を指定した双方向ストリームを検証する"""
    run_browser_e2e_send_order_stream(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_with_code(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で closeCode / reason を指定した切断を検証する"""
    run_browser_e2e_close_with_code(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_datagram_settings(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でデータグラムの動的設定を検証する"""
    run_browser_e2e_datagram_settings(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_connection_options(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で WebTransport オプション指定時の接続を検証する"""
    run_browser_e2e_connection_options(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_custom_headers(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でカスタムヘッダー付きの接続を検証する"""
    run_browser_e2e_custom_headers(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_stream_options(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) でストリーム作成オプション (sendOrder / waitUntilAvailable) を検証する"""
    run_browser_e2e_stream_options(webkit_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_stream(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) で双方向ストリームの close を検証する"""
    run_browser_e2e_close_stream(webkit_page, browser_server, certificate_hash_value)
