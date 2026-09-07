# UDP 系サーバー 3 種が idle timeout 起因の終了後にイベントを drain せず接続がリークする

- Created: 2026-09-06
- Completed: 2026-09-08
- Branch: feature/fix-server-idle-timeout-connection-leak
- Polished: 2026-09-07

## 目的

`quic.Server` / `h3.Server` / `http3.Server` は `handle_timeout()` 起因で接続が終了 (idle timeout など) した後にイベントキューを drain しない。結果、`CONNECTION_CLOSED` イベントが接続の event キューに滞留し、`on_connection_closed` は永久に発火せず、`_connections` / `_clients` からもエントリが消えない。`_connection.get_timeout()` は `closed_` 後に `None` を返すため二度と触られず、死んだ接続ごとに `ngtcp2_conn` + `SSL` + `SSL_CTX` がリークする。長時間稼働するサーバーで無制限に増える。接続終了時の全セッション一斉通知 (fan-out) は行わない (closed issue 0062 の確定判断と現行 h3 設計を維持するため。必要なら別 issue とする)。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` はタイマー分岐 (`for addr, connection in list(self._connections.items()): timeout = connection.get_timeout(); if timeout is not None and timeout <= 0: connection.handle_timeout(); await self._send_to(...)`) で `handle_timeout` と送信だけ行い、イベント drain (`_process_quic_events` 相当) を呼ばない
- `src/webtransport/h3/server.py` の `Server.run` と `src/webtransport/http3/server.py` の `Server.run` にも drain 欠落の同型構造がある (http3 は `is_closed()` 回収を既に持つが `next_event()` drain は無い)
- `src/bindings/quic.cpp` の `QuicConnection::handle_timeout` は idle timeout を検知すると `closed_ = true; push_event({QuicEventType::ConnectionClosed, ...})`
- `QuicConnection::get_timeout_ns` は `closed_` 後に `nullopt` を返す
- 実験 (高レベル quic Server + 実ソケットクライアント。検証済み): `idle_timeout_ns` 1 秒のサーバーに接続後、クライアント側 run を停止して沈黙させると、4 秒待っても `on_connection_closed` は発火せず `_connections` のエントリ (1 件) が残る
- h3 / http3 サーバーは同型構造のため同手順の類推が成立する (実行検証は実装時に行う)

## 設計方針

- 共通順序は drain→発火→削除とする。タイマー分岐で `handle_timeout` を呼んだ後にイベント drain を実行し、`CONNECTION_CLOSED` 到達で close コールバックを発火させ、同一 `for` 反復内 (`list()` 化済みのため削除安全) で `is_closed()` 真の接続を `_connections` / `_clients` から削除する
- quic.Server は受信分岐の drain ロジックを共通ヘルパ化し、タイマー分岐から呼ぶ。h3.Server は既存の `_process_quic_events` と `_process_webtransport_events` をタイマー分岐から呼ぶ (新規関数化はしない)。http3.Server はタイマー分岐に `next_event()` drain を追加し、既存の `is_closed()` 回収を継続する
- h3 / http3 Server で接続終了時に確立中セッションへ `on_session_closed` を一斉発火しない。bindings 層の 0062 確定判断 (接続終了をセッション終了と同一視しない) と現行 h3 設計 (`CONNECTION_CLOSED` 受信時は後続処理をスキップして復帰) を維持するためである。fan-out が必要になれば別 issue とする
- idle timeout / handshake timeout / ピアの CONNECTION_CLOSE のいずれも上記の共通順序で回収されるようにする

## 完了条件

- quic: idle timeout 発生後に `on_connection_closed` が発火し、`_connections` からエントリが削除されること
- h3: idle timeout 発生後に `_clients` からエントリが削除され、セッション終了の二重発火がないこと (fan-out しないことの確認を含む)
- http3: idle timeout 発生後に `_clients` からエントリが削除されること
- リーク判定はエントリ残留ゼロとイベント滞留ゼロで行い、RSS 実測は行わない (CI 環境変動のため)
- `tests/` に idle timeout での接続回収テストを quic / h3 / http3 の 3 モジュールに追加すること (高レベル Server + `idle_timeout_ns` 短縮 + クライアント沈黙の手順)
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- タイマー分岐で `handle_timeout` 後にイベント drain を実行し、`CONNECTION_CLOSED` 到達で close コールバックを発火させ、同一反復内で登録を外す共通順序にする
- quic.Server は 0149 の対応で充足しており、本 issue では h3 / http3 Server を修正する。h3 は既存の 2 処理をタイマー分岐から呼び、http3 は QUIC イベント drain を共通化して呼び出す。確立中セッションへの一斉通知は行わない
- `tests/test_e2e_webtransport_h3.py` と `tests/test_e2e_http3.py` に idle timeout での接続回収テストを追加する (quic は既存の回帰テストで充足する)
- 全 880 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
