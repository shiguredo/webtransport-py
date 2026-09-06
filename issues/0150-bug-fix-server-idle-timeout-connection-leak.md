# UDP 系サーバー 3 種が idle timeout 起因の終了後にイベントを drain せず接続がリークする

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-server-idle-timeout-connection-leak
- Polished: {YYYY-MM-DD}

## 目的

`quic.Server` / `h3.Server` / `http3.Server` は `handle_timeout()` 起因で接続が終了 (idle timeout など) した後にイベントキューを drain しない。結果、`CONNECTION_CLOSED` イベントが接続の event キューに滞留し、`on_connection_closed` / `on_session_closed` は永久に発火せず、`_connections` / `_clients` からもエントリが消えない。`_connection.get_timeout()` は `closed_` 後に `None` を返すため二度と触られず、死んだ接続ごとに `ngtcp2_conn` + `SSL` + `SSL_CTX` がリークする。長時間稼働するサーバーで無制限に増える。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` はタイマー分岐 (`for addr, connection in list(self._connections.items()): timeout = connection.get_timeout(); if timeout is not None and timeout <= 0: connection.handle_timeout(); await self._send_to(...)`) で `handle_timeout` と送信だけ行い、イベント drain (`_process_quic_events` 相当) を呼ばない
- `src/webtransport/h3/server.py` の `Server.run` と `src/webtransport/http3/server.py` の `Server.run` にも同型の構造がある
- `src/bindings/quic.cpp` の `QuicConnection::handle_timeout` は idle timeout を検知すると `closed_ = true; push_event({QuicEventType::ConnectionClosed, ...})`
- `QuicConnection::get_timeout_ns` は `closed_` 後に `nullopt` を返す
- 実験 (idle_timeout 1 秒でクライアントをソケット破棄で消す) で 4 秒後も `_connections` にエントリが残り、`is_closed()` は True、`next_event()` に `CONNECTION_CLOSED` が滞留、コールバック未発火
- h3 / http3 サーバーでも同じ結果を再現

## 設計方針

- 各 Server の `run()` のタイマー分岐で、`handle_timeout` を呼んだ後にイベント drain (`_process_quic_events` 相当) を実行する
- drain の結果 `is_closed()` になった接続は同一イテレーション内で `_connections` / `_clients` から削除し、`on_connection_closed` / `on_session_closed` を発火する
- イベント drain 処理を関数化し、受信分岐とタイマー分岐の両方から呼べるようにする (現状は受信分岐にインライン)
- draft-ietf-webtrans-http3-16 Section 6 の「セッション終了は CONNECT ストリームのクローズ」も踏まえ、h3 / http3 Server は QUIC の `CONNECTION_CLOSED` を受けたときに、確立中の全 WebTransport セッションに対して `on_session_closed` を発火する
- 修正後は idle timeout / handshake timeout / ピアの CONNECTION_CLOSE のいずれでも同じ経路を通ってエントリが回収されるようにする

## 完了条件

- idle timeout 発生後に `on_connection_closed` (h3 / http3 では `on_session_closed`) が発火すること
- idle timeout 発生後に `_connections` / `_clients` からエントリが削除されること
- 長時間稼働で `ngtcp2_conn` / `SSL_CTX` がリークしないこと (100 接続を idle timeout で消した後にプロセスの RSS 増分が有界)
- `tests/` に idle timeout での接続回収を検証するテストを quic / h3 / http3 の 3 モジュールに追加すること
- 既存のテスト全 822 件が引き続き通過すること
