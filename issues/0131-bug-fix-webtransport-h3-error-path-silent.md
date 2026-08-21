# WebTransport over HTTP/3 の H3Session が nghttp3 のエラーで closed_ を立てない問題を修正する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-webtransport-h3-error-path-silent
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/webtransport_h3.cpp` の `H3Session` が nghttp3 のエラー (`nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` の負値 return) を受けても `closed_ = true` を立てないため、WebTransport over HTTP/3 の高レベル `Client.run()` / `Server.run()` が `is_closed()` を確認しても True にならず、プロトコルエラー時に run() が終了しない (0107 で修正する `Http3Connection` と同種のバグが `H3Session` 側にも存在する)。この bug を修正し、`H3Session` の受信・送信両パスで nghttp3 の負値時に `closed_ = true` を立てる。

エラー情報 (H3 ワイヤーコード + メッセージ) をアプリへ通知する API 追加は 0130 のスコープに包含されるため、本 issue は `closed_` セットだけを扱う (0107 と同じ切り分け方針)。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` は `nghttp3_conn_read_stream2` の負値時に `H3EventType::Error` イベントを push する処理は持つが、`closed_ = true` は立てていない (`H3Event` の push 後もセッションは live 扱い)
- `src/bindings/webtransport_h3.cpp` の `H3Session::get_streams_to_send` は `nghttp3_conn_writev_stream` の負値時に `break` するだけで、`closed_ = true` も Error イベント push もしない
- 対比: 0107 で修正する `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `get_streams_to_send` は同種の負値時に `closed_ = true` を立てるようになる
- 対比: `src/bindings/http2.cpp` の `Http2Connection::receive` / `send` も同種の負値時に `closed_ = true` を立てる (0113 で活用済み)
- 結果として、WebTransport over HTTP/3 の高レベル層 (`src/webtransport/h3/client.py` / `server.py`) が `H3Session.is_closed()` を確認しても True にならず、プロトコルエラー時に run() が終了しないハングが残る
- 高レベル `Client.run` / `Server.run` にも現状 `is_closed()` チェック経路が無い (0113 / 0107 で HTTP/2 / HTTP/3 系は追加するが、WebTransport over HTTP/3 系は対象外)

## 設計方針

- `src/bindings/webtransport_h3.cpp`:
  - `H3Session::receive_stream_data` の `nghttp3_conn_read_stream2` 負値分岐で、既存の `H3EventType::Error` イベント push に加えて `closed_ = true` を立てる
  - `H3Session::get_streams_to_send` の `nghttp3_conn_writev_stream` 負値分岐で、`closed_ = true` を立てる (Error イベント push も同時に追加して `receive_stream_data` と対称にする)
  - error_message は既存パターン (`receive_stream_data`) と同じ `nghttp3_strerror` の返す文字列を使う
- `src/webtransport/h3/client.py` の `Client.run`:
  - 0107 の HTTP/3 版 `Client.run` に追加した `is_closed()` 検知パターンを踏襲する
  - HTTP/3 イベント処理ループ後、`self._session.is_closed()` を確認 (シンボル名は WebTransport over HTTP/3 高レベル層の実装に合わせる)
  - True なら QUIC 層の `close(H3_GENERAL_PROTOCOL_ERROR, "webtransport over http/3 protocol error")` を呼んで CONNECTION_CLOSE を送出、`self._running = False`
- `src/webtransport/h3/server.py` の `Server.run`:
  - 0107 の HTTP/3 版 `Server.run` に追加した per-client is_closed() チェックパターン (通常経路 + TimeoutError 分岐と同じ層の per-client タイマー処理) を踏襲する
- `H3_GENERAL_PROTOCOL_ERROR` 定数は 0107 で新設される `src/webtransport/http3/constants.py` を再利用する (HTTP/3 と WebTransport over HTTP/3 は同じ RFC 9114 のエラーコード体系)

## 完了条件

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `get_streams_to_send` が nghttp3 の負値 return 時に `closed_ = true` を立てるようになっている
- `src/webtransport/h3/client.py` の `Client.run` / `src/webtransport/h3/server.py` の `Server.run` に `is_closed()` チェックと QUIC `close()` 呼び出しが追加され、0107 の HTTP/3 版と対称になっている
- `AGENTS.md`「モックやスタブは絶対に利用しないこと」に従い、実 Client / Server を組み合わせた e2e で回帰確認する
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する
- 既存 e2e テスト (`tests/test_e2e_webtransport_h3.py` 等) がすべて pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` の `nghttp3_conn_read_stream2` 負値分岐に `closed_ = true;` を追加 (既存 Error イベント push はそのまま)
- `src/bindings/webtransport_h3.cpp` の `H3Session::get_streams_to_send` の `nghttp3_conn_writev_stream` 負値分岐に `closed_ = true;` と `H3EventType::Error` イベント push を追加 (`receive_stream_data` と対称)
- `src/webtransport/h3/client.py` / `server.py` に 0107 と同じパターンで is_closed() チェック + QUIC `close(H3_GENERAL_PROTOCOL_ERROR, ...)` + 高レベル run() 終了処理を追加
- `tests/test_e2e_webtransport_h3.py` に QUIC CONNECTION_CLOSED 経路の回帰テストと、可能ならフレームエラー経路の直接検証テストを追加 (0107 と同じ方針)
- 変更対象: `src/bindings/webtransport_h3.cpp` / `src/webtransport/h3/client.py` / `src/webtransport/h3/server.py` / `tests/test_e2e_webtransport_h3.py`
- 変更対象外: `src/bindings/http3.cpp` (0107 のスコープ)、`src/bindings/http2.cpp` (0113 で完了)、`Http3EventType::Error` を http3 側にも追加する話 (0130 のスコープ)

## 依存関係

- 本 issue は 0107 (closed 予定) と 0130 (open) の完了を前提としない (WebTransport over HTTP/3 は独立したセッション層) が、`H3_GENERAL_PROTOCOL_ERROR` constant を 0107 で新設される `src/webtransport/http3/constants.py` から import するため、0107 完了後に着手するのが自然
- 0130 と本 issue はどちらも WebTransport over HTTP/3 の Error イベントを扱うが、0130 は http3.cpp 側 (素の HTTP/3) を対象とし、本 issue は webtransport_h3.cpp 側の closed_ セット漏れだけを対象とする (通知 API 全体の統合設計は将来別 issue で扱う可能性あり)
