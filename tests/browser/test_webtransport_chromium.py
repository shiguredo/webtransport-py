"""Chromium を使った WebTransport E2E テスト

実ブラウザ (Chromium) の WebTransport API から echo サーバーへの接続と
送受信を検証する。検証ロジックはヘルパー (helpers.py) にあり、ここでは
Chromium ブラウザのページとサーバーフィクスチャを渡すだけである。
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
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium から WebTransport サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_send_order_stream(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium で sendOrder を指定した双方向ストリームを検証する"""
    run_browser_e2e_send_order_stream(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_with_code(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium で closeCode / reason を指定した切断を検証する"""
    run_browser_e2e_close_with_code(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_datagram_settings(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium でデータグラムの動的設定を検証する"""
    run_browser_e2e_datagram_settings(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_connection_options(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium で WebTransport オプション指定時の接続を検証する"""
    run_browser_e2e_connection_options(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_custom_headers(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium でカスタムヘッダー付きの接続を検証する"""
    run_browser_e2e_custom_headers(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_stream_options(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium でストリーム作成オプション (sendOrder / waitUntilAvailable) を検証する"""
    run_browser_e2e_stream_options(chromium_page, browser_server, certificate_hash_value)


def test_browser_e2e_close_stream(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium で双方向ストリームの close を検証する"""
    run_browser_e2e_close_stream(chromium_page, browser_server, certificate_hash_value)
