# http3.Server.stop() が CONNECTION_CLOSE を送出しない問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-http3-server-stop-close
- Polished: 2026-08-24

## 目的

高レベル `http3.Server.stop()` が、QUIC 接続の close() が生成する CONNECTION_CLOSE パケットを送出せずにソケットを閉じる問題を修正する。ピアは接続が切断された理由 (エラーコード) を受け取れない。

## 現状

- `src/webtransport/http3/server.py` の `Server.stop` は `client.quic_connection.close()` を呼ぶが、生成されたパケットの送出 (`_send_to` 相当) をせずにソケットを閉じる
- `src/webtransport/quic/server.py` の `Server.stop` と `src/webtransport/h3/server.py` の `Server.stop` は close() 生成の CONNECTION_CLOSE を送出する
- CHANGES.md の「close() が生成した CONNECTION_CLOSE パケットを送出しない問題を修正する」はこの層では未適用

## 設計方針

- `http3.Server.stop` で close() 生成パケットを送出してからソケットを閉じる (quic / h3 層と対称の実装にする)

## 完了条件

- stop() 時に CONNECTION_CLOSE がピアへ送出される
- テストが追加される

## 解決方法

- `src/webtransport/http3/server.py` の `Server.stop` を quic / h3 層の `Server.stop` と同じ構造に修正した: 接続スナップショット (`list(self._clients.items())`。並行する run() が self._clients を del しても壊れない) → close() 呼び出し → 生成された CONNECTION_CLOSE パケットの送出 (`_send_to`。1 接続の送出失敗で残りへの送出が中断されないよう OSError を接続ごとに隔離) → finally で後始末 (clear + ソケットクローズ) を保証
- テスト: `tests/test_e2e_http3.py` の `test_server_stop_delivers_connection_close` (stop() の CONNECTION_CLOSE を受信してクライアントの run() が自然終了することを検証。修正前はパケット未送出のためタイムアウトで失敗する)
