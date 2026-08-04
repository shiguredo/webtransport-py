# nghttp3 のストリーム送信拡張 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h3-message-ext
- Polished: {YYYY-MM-DD}

## 目的

HTTP/3 のトレーラ送信・1xx レスポンス・graceful shutdown・書き込み側シャットダウンを Python から行えるようにする。HTTP/3 のメッセージング機能として標準的な対応であり、現在はトレーラや 1xx を扱う手段が無い。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection` は `submit_request` / `submit_response` / `send_data` / `goaway` を公開しているが、トレーラ・1xx・shutdown notice は扱えない
- `goaway()` は `nghttp3_conn_shutdown` に相当するが、本家の推奨手順 (shutdown notice → RTT 待ち → shutdown) を踏んでいない
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_submit_trailers`: トレーラ送信
  - `nghttp3_conn_submit_info`: 1xx レスポンス (Informational Response)
  - `nghttp3_conn_submit_shutdown_notice`: graceful shutdown の開始通知 (GOAWAY 相当)
  - `nghttp3_conn_shutdown_stream_write`: ストリームの書き込み側シャットダウン

## 設計方針

- `Http3Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http3.Http3Connection`)
- `submit_trailers` はストリーム ID とヘッダーを受け取り、`send_data(stream_id, data, fin=True)` の後に呼べる形で公開する
- `submit_info` はストリーム ID とヘッダーを受け取り、レスポンス送信前に呼べる形で公開する
- `submit_shutdown_notice` は既存 `goaway()` の実装と整合させ、`goaway()` が内部的に shutdown notice を送る形に変更するか、独立メソッドとして追加するかを実装時に判断する
- `shutdown_stream_write` はストリーム ID を引数に取り、書き込み側を閉じる
- WebTransport (H3Session) ではトレーラ・1xx は使わないため `H3Session` には追加しない
- `src/webtransport/http3.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からトレーラを送信できる
- Python から 1xx レスポンスを送信できる
- Python から graceful shutdown (shutdown notice) を開始できる
- Python からストリームの書き込み側をシャットダウンできる
- モックなしのテストで、各 API が動作することを確認する
