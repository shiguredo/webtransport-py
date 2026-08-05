"""Chromium を使った WebTransport E2E テスト

実ブラウザ (Chromium) の WebTransport API から echo サーバーへの接続と
送受信を検証する。検証ロジックは共通実装 (_webtransport_e2e.py) にあり、
ここでは Chromium ブラウザのページとサーバーフィクスチャを渡すだけである。
"""

import pytest
from _webtransport_e2e import run_browser_e2e_webtransport
from playwright.sync_api import Page

pytestmark = pytest.mark.browser


def test_browser_e2e_webtransport(
    chromium_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """Chromium から WebTransport サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(chromium_page, browser_server, certificate_hash_value)
