# http3.Server.stop() が CONNECTION_CLOSE を送出しない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
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
