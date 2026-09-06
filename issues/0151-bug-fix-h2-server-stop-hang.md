# h2.Server.stop() / http2.Server.stop() がアクティブ接続中に復帰しない

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-server-stop-hang
- Polished: {YYYY-MM-DD}

## 目的

`h2.Server.stop()` / `http2.Server.stop()` はクライアントが接続中に呼ぶと復帰しない。`_handle_client` のループが `_running` フラグを見ておらず、Python 3.13 以降の `asyncio.Server.wait_closed()` は全接続の終了を待つため、TCP EOF まで永久に待つ。`async with server:` の `__aexit__` も同じ経路のため、examples の Ctrl+C 相当や pytest fixture のクリーンアップでもハングする。e2e テストは全て `client.close()` 後に `stop()` するため露見していないが、実運用のシャットダウンで問題化する。

## 現状

- `src/webtransport/http2/server.py` の `Server._handle_client` は `while True: ...` で `self._running` を一切参照しない
- `src/webtransport/http2/server.py` の `Server.stop` は `self._running = False; if self._server is not None: self._server.close(); await self._server.wait_closed()`
- `src/webtransport/h2/server.py` の `Server._handle_client` と `Server.stop` にも同型の構造がある
- 実験で `asyncio.wait_for(server.stop(), 3.0)` が h2 / http2 とも `TimeoutError` (3 秒以上復帰しない)
- 対称の `h3.Server.stop()` は接続を明示的に close するため復帰する

## 設計方針

- `_handle_client` のループ条件を `while self._running` にする
- `stop()` で追加でハンドラタスクを追跡してキャンセルするか、`asyncio.Server.close_clients()` (Python 3.13+) を呼ぶ
- キャンセル時にクライアントに GOAWAY (可能ならセッションに WT_CLOSE_SESSION) を送出してから TCP を閉じる (draft-ietf-webtrans-http2-15 Section 3.4 / RFC 9113 Section 6.8 の graceful shutdown に合わせる)
- `close()` 中の `writer.close()` / `wait_closed()` の例外は現状どおり握らず、必要なら明示的に `logger.warning` する
- `async with server:` の `__aexit__` も同経路のため副次的に修正される

## 完了条件

- クライアント接続中の `server.stop()` が数百 ms 以内に復帰すること
- キャンセル時にクライアントが接続終了を検知できること (GOAWAY またはピア切断)
- `tests/` に「クライアント接続中に stop() が復帰する」テストを h2 / http2 に追加すること
- 既存のテスト全 822 件が引き続き通過すること
