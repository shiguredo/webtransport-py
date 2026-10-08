# 実ブラウザ E2E に Playwright Firefox を追加する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/test-add-firefox-browser-e2e
- Reporter: @voluntas

## 目的

`tests/browser/` の実ブラウザ E2E は Chromium と WebKit だけを対象にしており、
Firefox から WebTransport over HTTP/3 で接続・送受信が成立するかを検証できない。
Firefox の WebTransport 実装は Chromium / WebKit と独立しており、サーバー実装の
相互運用を確かめる対象として必要である。実測では Playwright 1.62.0 同梱の
Firefox 153.0 から webtransport-py の h3 サーバーへ接続し、既存ヘルパーの
8 検証 (接続確立・双方向 / 単方向ストリーム・データグラム・close) がすべて成立した。

## 現状

- `tests/browser/conftest.py` は `chromium_browser` と `webkit_browser` の fixture だけを
  提供し、Firefox 用のテストファイルは無い
- Firefox 153.0 から接続すると、公開サイト (`/webtransport-devtools`) から
  `https://127.0.0.1:PORT/webtransport` への接続が "Connecting..." のまま確立しない。
  Firefox 153 の Local Network Access (LNA) が公開サイトからローカルアドレスへの接続を
  権限プロンプトで確認するためであり、ヘッドレスでは応答できず
  `network.lna.prompt.timeout` (既定 300000 ms) まで待ってキャンセルされる。
  `network.lna.enabled` / `network.lna.blocking` の既定はいずれも true である
- Playwright の `grant_permissions` は `local-network-access` を Firefox の
  `local-network` / `loopback-network` 権限へ対応付けている。事前付与により
  接続が確立することを実測で確認した (`network.lna.blocking` を false にする方法でも
  同様に動作する)
- Firefox 153 は WebTransport over HTTP/2 に対応していない。HTTP/2 の実装
  (`Http2WebTransportSession`) はバイナリに含まれるが DOM から使う統合が未着手であり、
  実測でも h2 サーバーへ接続すると TCP コネクションすら張られず (QUIC のみを試行)、
  接続は確立しない。したがって `tests/browser/test_webtransport_webkit_h2.py` は
  Firefox へ展開しない
- Firefox 153 は `getStats` / `WebTransportWriter.waitUntilAvailable` /
  `incomingMaxBufferedDatagrams` などの API が未実装だが、`tests/browser/helpers.py` の
  8 検証はすべて通過する

## 設計方針

- `tests/browser/conftest.py` に `firefox_browser` / `firefox_page` fixture を追加する。
  ページ用 fixture は `new_context(permissions=["local-network-access"])` で LNA の権限を
  事前付与する。Chromium で `--disable-features=LocalNetworkAccessChecks` を指定している
  のと同じ目的だが、チェック自体は有効のまま「明示的に許可する」方が対象が狭い
- `tests/browser/test_webtransport_firefox.py` を Chromium と同じ 8 検証
  (`run_browser_e2e_*`) で追加する
- Firefox の LNA 回避方法と Firefox が HTTP/2 非対応であることを fixture の docstring に
  記録する。ブラウザ一覧を持つ docstring (`tests/browser/conftest.py` /
  `tests/browser/helpers.py`) と `pyproject.toml` の marker 説明を Firefox 込みに更新する
- 変更対象: `tests/browser/conftest.py` / `tests/browser/test_webtransport_firefox.py` /
  `tests/browser/helpers.py` / `pyproject.toml`

## 完了条件

- `uv run pytest tests/browser -m browser --timeout=60` で Firefox の 8 検証を含む
  全テストが通過する
- Firefox のテストが繰り返し実行しても失敗しない (3 回以上連続で成功する)
- Firefox が HTTP/2 非対応であることと LNA の回避方法が docstring に記録されている
- 通常の `make test` と CI の collection 対象外のままである (`--ignore=tests/browser`)

## 解決方法
