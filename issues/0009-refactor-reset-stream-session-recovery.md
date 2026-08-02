# サーバー側の STREAM_RESET イベントでセッション ID をストリーム情報から復元する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/refactor-reset-stream-session-recovery
- Polished: {YYYY-MM-DD}

## 目的

`Server` の `on_stream_reset` コールバックが、リセットされたストリームの属するセッションの ID を正しく受け取れるようにする。現在はセッション ID 集合の先頭要素に依存しており、同一クライアントアドレスから複数セッションを確立した構成では誤ったセッション ID が渡される。

## 現状

- `src/webtransport/h3/server.py` の `Server._process_quic_events` は `STREAM_RESET` イベントのたびに `H3Session.get_session_ids()` の先頭要素をセッション ID として `on_stream_reset` に渡す
- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` は `stream_info_` からストリーム情報を削除する際にセッション ID を返さないため、Python 側でストリーム ID からセッションを復元できない
- 単一セッション構成では正しく動くが、同一クライアントアドレスから複数セッションを持つ構成では、リセットされたストリームと無関係なセッション ID が渡される

## 設計方針

- `H3Session::close_stream` がリセットされたストリームのセッション ID を返すようにし、Python 側の `get_session_ids()` の先頭要素への依存をやめる
- ストリーム情報 (`stream_info_`) にはセッション ID が記録済みのため、削除前に取り出すだけで実現できる

## 完了条件

- `on_stream_reset` に渡されるセッション ID が、リセットされたストリームの属するセッションの ID である
- モックなしの e2e テストで検証できる (同一クライアントアドレスから 2 セッションを確立し、2 つ目のセッションのサーバー起点ストリームをクライアントがリセットしたときに 2 つ目のセッション ID が渡される)
