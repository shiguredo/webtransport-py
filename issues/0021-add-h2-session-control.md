# nghttp2 のセッション制御 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h2-session-control
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 セッションの即時切断とウィンドウサイズの動的調整を Python から行えるようにする。現在は graceful shutdown (`goaway()`) のみで、エラー時の即時切断やフロー制御の動的変更ができない。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `goaway(error_code)` のみを公開しており、`nghttp2_session_terminate_session2` による即時切断ができない
- ウィンドウサイズは `Http2Config` のビルド時値に固定されており、動的な変更手段が無い
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_session_terminate_session2`: GOAWAY を送信して即時にセッションを終了 (last_stream_id を指定可能)
  - `nghttp2_session_set_local_window_size`: ローカルウィンドウサイズの動的変更 (コネクション / ストリーム)

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Http2Connection`)
- `terminate_session` は既存 `goaway()` と明確に区別し、即時切断であることをドキュメントで明示する。引数は `error_code` と `last_stream_id` を受け付ける
- `set_local_window_size` はコネクション全体とストリーム単位の両方を扱える形で公開する
- WebTransport over HTTP/2 (`H2Session`) には追加しない
- `src/webtransport/http2.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python から GOAWAY を送信せずにセッションを即時終了できる
- Python からコネクション / ストリームのローカルウィンドウサイズを動的に変更できる
- モックなしのテストで、各 API が動作することを確認する
