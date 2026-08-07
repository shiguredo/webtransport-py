# 実ブラウザ (WebKit) を使った WebTransport over HTTP/2 E2E テストを追加する

- Created: 2026-08-07
- Completed: 2026-08-07
- Branch: feature/add-browser-e2e-webkit-h2
- Polished: YYYY-MM-DD

## 目的

webtransport-py が実装する WebTransport over HTTP/2 (draft-ietf-webtrans-http2) を実ブラウザ (WebKit / Safari) の WebTransport API から検証する。現在のブラウザ E2E テストは WebTransport over HTTP/3 のみを対象としており、HTTP/2 の相互運用性は検証されていないため。

## 現状

- `tests/browser/` の E2E テストは WebTransport over HTTP/3 (`webtransport.h3.Server`) のみを対象としている。issue 0004 の設計方針で「WebTransport over HTTP/2 は Chromium が対応していないため対象外」としていたが、Safari (WebKit) は WebTransport over HTTP/2 に対応している
- `tests/browser/conftest.py` の `BrowserWebTransportServer` は `webtransport.h3.Server` をラップした echo サーバーで、イベントを共有キューに積み `wait_event()` で観測する
- `tests/browser/helpers.py` の検証ヘルパーのイベント展開は h3 の形状 (末尾に addr) に依存するが、addr はどのヘルパーでも使われていない
- WebTransport over HTTP/2 のサーバー実装 (`src/webtransport/h2/server.py`) は SETTINGS で `SETTINGS_ENABLE_CONNECT_PROTOCOL` と `SETTINGS_WT_ENABLED` を送出するため、Safari から WebTransport 対応サーバーとして認識される
- サーバーが開始する単方向ストリームのデータが WebKit で受信できない問題があり (issue 0033)、本テストの「サーバーからの単方向ストリーム送信」の検証が通らない。issue 0033 の修正が必要

## 設計方針

- `tests/browser/conftest.py` に `webtransport.h2.Server` をラップした h2 echo サーバー (`BrowserWebTransportServerH2`) と `browser_server_h2` フィクスチャを追加する。h3 版と共有するスレッド・共有キューの仕組みは共通クラス (`BrowserWebTransportServerBase`) に抽出する
- h2 サーバーのイベントは addr を含まない (例: `on_stream_data` が (session_id, stream_id, data)) ため、`tests/browser/helpers.py` のイベント展開をプロトコル非依存にする (addr の有無を `*_` で吸収し、ページ操作と `stream_id % 4` の判定は h3 / h2 で共通化する)
- DevTools テストページ (https://moqt-devtools.shiguredo.app/webtransport-devtools) は `https://` URL への接続で HTTP/2 を選択し、reliability 表示が reliable-only になり HTTP/2 バッジが表示される。この表示を確認することで WebTransport over HTTP/2 で接続されたことを検証する
- WebTransport over HTTP/2 は `requireUnreliable` を指定すると HTTP/3 へフォールバックが試行されるため、接続オプションのテストは `requireUnreliable` を指定しない (congestionControl のみ)
- テストファイルは `tests/browser/test_webtransport_webkit_h2.py` として追加し、既存の h3 テスト (Chromium / WebKit) は変更しない

## 完了条件

- `tests/browser/test_webtransport_webkit_h2.py` のテスト一式が WebKit (Safari) で WebTransport over HTTP/2 サーバーへの接続と送受信を検証できる
- 既存の h3 ブラウザテスト (Chromium / WebKit) が引き続き通る

## 解決方法

- `tests/browser/test_webtransport_webkit_h2.py` を追加し、WebKit (Safari) の WebTransport over HTTP/2 接続と送受信を検証する 8 テストを実装した。接続オプションのテストは `requireUnreliable` を指定せず、HTTP/2 バッジ (reliability = reliable-only) の表示でプロトコルが HTTP/2 であることを確認する
- `tests/browser/conftest.py` に共通基盤 (`BrowserWebTransportServerBase`) と h2 echo サーバー (`BrowserWebTransportServerH2`)・`browser_server_h2` フィクスチャを追加した
- `tests/browser/helpers.py` のイベント展開をプロトコル非依存にし (addr の有無を `*_` で吸収)、h3 / h2 でヘルパーを共通化した
- 前提となった WebKit 相互運用の修正 (サーバー開始ストリームのデータ未達) は issue 0033 で対応した
- ブラウザ E2E テスト 24 件 (Chromium h3 8 / WebKit h3 8 / WebKit h2 8) と単体テスト 419 件が通ることを確認した