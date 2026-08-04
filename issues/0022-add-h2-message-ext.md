# nghttp2 のストリーム送信拡張 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h2-message-ext
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 のトレーラ送信・優先度更新 (RFC 9218)・Server Push・ALPN 選択を Python から行えるようにする。HTTP/2 のメッセージング機能として標準的な対応であり、現在はリクエスト / レスポンス / データ送信のみが利用可能。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `submit_request` / `submit_response` / `send_data` / `reset_stream` / `goaway` / `ping` を公開しているが、トレーラ・優先度・Server Push は扱えない
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_submit_trailer`: トレーラ送信
  - `nghttp2_submit_priority_update`: RFC 9218 の優先度更新フレーム送信
  - `nghttp2_session_change_stream_priority`: ストリームの優先度の動的変更 (RFC 7540 優先度ツリー)
  - `nghttp2_submit_push_promise`: Server Push の宣言
  - `nghttp2_select_alpn`: ALPN プロトコルの選択 (サーバー用ユーティリティ)

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Http2Connection`)
- `submit_trailer` はストリーム ID とヘッダーを受け取り、`send_data(stream_id, data, eof=True)` の後に呼べる形で公開する
- 優先度は RFC 9218 の `priority_update` と RFC 7540 の `change_stream_priority` の両方を扱える形で公開するか、RFC 9218 のみにするかは実装時に判断する
- `submit_push_promise` はヘッダーと promise ストリーム ID を返す形で公開する (受信側のハンドリングはイベントで通知する)
- `select_alpn` はサーバー側の ALPN 選択ユーティリティとして公開する
- WebTransport over HTTP/2 (`H2Session`) には追加しない
- `src/webtransport/http2.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からトレーラを送信できる
- Python からストリーム優先度を更新できる
- Python から Server Push を宣言できる
- Python から ALPN を選択できる
- モックなしのテストで、各 API が動作することを確認する
