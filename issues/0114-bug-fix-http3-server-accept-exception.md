# http3.Server.run() が非 Initial パケットの RuntimeError で死ぬ問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-server-accept-exception
- Polished: 2026-08-24

## 目的

高レベル `http3.Server.run()` が、未知アドレスからの非 Initial パケット (接続終了済みアドレスからの追従パケット等) で例外を投げて死ぬ問題を修正する。

## 現状

- `src/webtransport/http3/server.py` の `Server.run` は `addr not in self._clients` の場合に `self._accept_connection(addr, data)` を無条件で呼び、`except TimeoutError` しか捕捉しない
- `quic.Connection.accept` は非 Initial パケットで RuntimeError を投げる設計であり、`src/webtransport/quic/server.py` の `Server.run` と `src/webtransport/h3/server.py` の `Server.run` は RuntimeError を捕捉して破棄している
- `http3.Server.run` だけが捕捉しておらず、追従パケットで run() が例外終了する

## 設計方針

- `http3.Server.run` の accept 呼び出しを RuntimeError 捕捉で包み、非 Initial パケットは破棄する (quic / h3 層と対称の実装にする)

## 完了条件

- 非 Initial パケットが送られても run() が継続する
- テストが追加される
