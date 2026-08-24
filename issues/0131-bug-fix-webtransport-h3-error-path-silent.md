# WebTransport over HTTP/3 の H3Session が nghttp3 のエラーで closed_ を立てない問題を修正する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-webtransport-h3-error-path-silent
- Polished: 2026-08-24

## 目的

`src/bindings/webtransport_h3.cpp` の `H3Session` が nghttp3 の接続エラー (`nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` の負値 return) を受けても `closed_ = true` を立てないため、WebTransport over HTTP/3 の高レベル `Client.run()` / `Server.run()` が `is_closed()` を確認しても True にならず、接続エラー時に run() が終了しない (closed 済み 0107 が修正した `Http3Connection` と同種のバグが `H3Session` 側にも存在する)。この bug を修正し、`H3Session` の受信・送信両パスで接続エラー時に `closed_ = true` を立てる。

エラー情報 (H3 ワイヤーコード + メッセージ) をアプリへ通知する API 追加は本 issue のスコープ外とする。0130 は素の HTTP/3 (`Http3Connection`) 側のみを対象としており、`H3Session` 側の通知設計は含まれない (通知 API 全体の統合設計は将来別 issue で扱う)。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` は `nghttp3_conn_read_stream2` の負値時に `H3EventType::Error` イベントを push する処理は持つが、`closed_ = true` は立てていない (`H3Event` の push 後もセッションは live 扱い)
- `src/bindings/webtransport_h3.cpp` の `H3Session::get_streams_to_send` は `nghttp3_conn_writev_stream` の負値時に `break` するだけで、`closed_ = true` も Error イベント push もしない
- 対比: closed 済み 0107 が見直した `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `get_streams_to_send` は同種の負値時に `closed_ = true` を立てる
- 対比: `src/bindings/http2.cpp` の `Http2Connection::receive` / `send` も同種の負値時に `closed_ = true` を立てる
- `H3Session::shutdown_cb` は GOAWAY 受信時に `closed_ = true` を立てる (既存。プロトコルエラーとは別の普通の終了処理であり、本 issue で変更しない)
- 結果として、WebTransport over HTTP/3 の高レベル層 (`src/webtransport/h3/client.py` / `server.py`) が `H3Session.is_closed()` を確認しても True にならず、接続エラー時に run() が終了しないハングが残る
- 高レベル `Client.run` / `Server.run` にも現状 `is_closed()` チェック経路が無い (closed 済み 0113 / 0107 で HTTP/2 / HTTP/3 系は追加済みだが、WebTransport over HTTP/3 系は対象外)

## 設計方針

- **エラー値の分類**: nghttp3 の負値はすべてが接続エラーではない。接続終了が必要なのは `nghttp3_err_is_fatal()` (公開 API。NGHTTP3_ERR_FATAL = -900 未満) が真になる値であり、`H3Session` の負値分岐は接続エラーのみ `closed_ = true` とする。ストリームレベルのエラーである `NGHTTP3_ERR_H3_MESSAGE_ERROR` (-611) は open 中 0096 の CONNECT ストリームリセット処理のスコープであり、closed_ = true にせず接続を継続する (`NGHTTP3_ERR_WT_SESSION_GONE` 系は nghttp3 の内部処理で読み取りが正値で返るため、負値分岐には入らない)
- `src/bindings/webtransport_h3.cpp`:
  - `H3Session::receive_stream_data` の `nghttp3_conn_read_stream2` 負値分岐で、`nghttp3_err_is_fatal()` が真の場合は既存の `H3EventType::Error` イベント push に加えて `closed_ = true` を立てる (Error イベント push の扱い・error_message も `nghttp3_strerror` を流用する既存パターンのまま)
  - `H3Session::get_streams_to_send` の `nghttp3_conn_writev_stream` 負値分岐で、`nghttp3_err_is_fatal()` が真の場合は `closed_ = true` を立てる (Error イベント push も `receive_stream_data` と対称に追加する)
- `src/webtransport/h3/client.py` の `Client.run`:
  - closed 済み 0107 の HTTP/3 版 `Client.run` に追加した `is_closed()` 検知パターンを踏襲する
  - HTTP/3 イベント処理ループ後、`self._session.is_closed()` を確認する (シンボル名は WebTransport over HTTP/3 高レベル層の実装に合わせる)
  - True なら QUIC 層の `close(H3_GENERAL_PROTOCOL_ERROR, "webtransport over http/3 protocol error")` を呼んで CONNECTION_CLOSE を送出し、`self._running = False`
- `src/webtransport/h3/server.py` の `Server.run`:
  - closed 済み 0107 の HTTP/3 版 `Server.run` に追加した per-client is_closed() チェックパターンを踏襲する
- `H3_GENERAL_PROTOCOL_ERROR` 定数は closed 済み 0107 が新設した `src/webtransport/http3/constants.py` を再利用する (HTTP/3 と WebTransport over HTTP/3 は同じ RFC 9114 のエラーコード体系)

## 完了条件

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `get_streams_to_send` が接続エラー (`nghttp3_err_is_fatal()` が真) の負値 return 時に `closed_ = true` を立てるようになっている
- `NGHTTP3_ERR_H3_MESSAGE_ERROR` の場合、closed_ = true にせず 0096 (open) のリセット処理と干渉しない
- `src/webtransport/h3/client.py` の `Client.run` / `src/webtransport/h3/server.py` の `Server.run` に `is_closed()` チェックと QUIC `close()` 呼び出しが追加され、closed 済み 0107 の HTTP/3 版と対称になっている
- `AGENTS.md`「モックやスタブは絶対に利用しないこと」に従い、実 Client / Server を組み合わせた e2e で回帰確認する
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する
- 既存 e2e テスト (`tests/test_e2e_webtransport_h3.py` 等) がすべて pass、`ruff format` / `ruff check` / `ty check` 通過

## 変更対象・対象外

- 対象: `src/bindings/webtransport_h3.cpp` / `src/webtransport/h3/client.py` / `src/webtransport/h3/server.py` / `tests/test_e2e_webtransport_h3.py` / CHANGES.md (## develop への [FIX])
- 対象外: `src/bindings/http3.cpp` (closed 済み 0107 のスコープ) / `src/bindings/http2.cpp` (変更不要) / `H3Session` 側のエラー情報通知 API (将来別 issue)

## 依存関係

- 本 issue は 0107 (closed 済み) と 0130 (open) の完了を前提としない (WebTransport over HTTP/3 は独立したセッション層) が、`H3_GENERAL_PROTOCOL_ERROR` 定数を closed 済み 0107 が新設した `src/webtransport/http3/constants.py` から import するため、その実装が存在することが前提
- 0096 (open) と同一分岐 (receive_stream_data の負値分岐) を触る。エラー値で分離する (接続エラーは本 issue、`NGHTTP3_ERR_H3_MESSAGE_ERROR` は 0096) ため、両者は協調して付ける
