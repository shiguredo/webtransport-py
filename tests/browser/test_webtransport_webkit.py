"""WebKit (Safari) を使った WebTransport E2E テスト

実ブラウザ (WebKit / Safari) の WebTransport API から echo サーバーへの
接続と送受信を検証する。検証ロジックはヘルパー (helpers.py) にあり、ここでは
WebKit ブラウザのページとサーバーフィクスチャを渡すだけである。
"""

import pytest
from helpers import run_browser_e2e_webtransport
from playwright.sync_api import Page

pytestmark = pytest.mark.browser


def test_browser_e2e_webtransport(
    webkit_page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebKit (Safari) から WebTransport サーバーへの接続と送受信を検証する"""
    run_browser_e2e_webtransport(webkit_page, browser_server, certificate_hash_value)
