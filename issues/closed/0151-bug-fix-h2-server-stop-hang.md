# h2.Server.stop() / http2.Server.stop() がアクティブ接続中に復帰しない

- Created: 2026-09-06
- Completed: 2026-09-08
- Branch: feature/fix-h2-server-stop-hang
- Polished: 2026-09-07

## 目的

`h2.Server.stop()` / `http2.Server.stop()` はクライアントが接続中に呼ぶと復帰しない。`_handle_client` のループが `_running` フラグを見ておらず、Python 3.12 以降の `asyncio.Server.wait_closed()` は全接続の終了を待つため、TCP EOF まで永久に待つ。`async with server:` の `__aexit__` も同じ経路のため、examples の Ctrl+C 相当や pytest fixture のクリーンアップでもハングする。確認した範囲の e2e テストは全て `client.close()` 後に `stop()` するため露見していないが、実運用のシャットダウンで問題化する。

## 現状

- `src/webtransport/http2/server.py` の `Server._handle_client` は `while True: ...` で `self._running` を一切参照しない
- `src/webtransport/http2/server.py` の `Server.stop` は `self._running = False; if self._server is not None: self._server.close(); await self._server.wait_closed()`
- `src/webtransport/h2/server.py` の `Server._handle_client` と `Server.stop` にも同型の構造がある (h2 / http2 同一手順で修正する)
- 実験 (高レベル Server + 実ソケットクライアント。検証済み): GET 送受信後に `asyncio.wait_for(server.stop(), 3.0)` を呼ぶと h2 / http2 とも `TimeoutError` (3 秒以上復帰しない)
- 対称の `h3.Server.stop()` は接続を明示的に close するため復帰する

## 設計方針

- `_handle_client` のループ条件を `while self._running` にする。既存の break 条件 (TCP EOF / `is_closed()`) は維持し、`_running` は stop 時の追加の脱出条件とする
- `stop()` は `_running = False` の後に `asyncio.Server.close_clients()` を呼んで全クライアント transport を閉じ、既存どおり `close()` 後に `wait_closed()` で待つ。対応 Python は 3.14 以降のみのためフォールバックは要らない。ハンドラタスクの手動追跡は行わない (asyncio が管理する)
- 停止時の GOAWAY / WT_CLOSE_SESSION 送出は行わない。TCP 切断によるピアの切断検知で足りる。graceful shutdown が必要になれば別 issue とする
- `async with server:` の `__aexit__` も同経路のため副次的に修正される

## 完了条件

- クライアント接続中の `server.stop()` が呼び出しから 500 ms 以内に復帰すること (アプリコールバック即時復帰の通常条件で、`time.monotonic()` の wall time で測定する。ポーリング間隔 0.1 秒との関係で 500 ms を上限とする)
- 停止時にクライアントが TCP 切断を検知できること
- `tests/` に「クライアント接続中に stop() が復帰する」テストを h2 / http2 に追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- `_handle_client` の受信ループ条件を `while True` から `while self._running` に変える。既存の break 条件 (TCP EOF / `is_closed()`) は維持する
- `stop()` は `_running = False` の後に `close_clients()` で全クライアント transport を閉じてから `close()` と `wait_closed()` を行う
- 停止時の GOAWAY 送出は行わない (TCP 切断による検知で足りる)
- `tests/test_e2e_webtransport_h2.py` と `tests/test_e2e_http2.py` に接続中の停止復帰テストを追加する (500 ms 以内の復帰は完了条件の指定どおりに断言する)
- http2 側も同型不具合のため同時修正する
- 全 882 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
