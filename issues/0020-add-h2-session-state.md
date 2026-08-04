# nghttp2 のセッション状態・設定確認 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h2-session-state
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 セッションの SETTINGS・ウィンドウサイズ・送信キューサイズ・ストリーム上限を Python から確認できるようにし、フロー制御の監視とバックプレッシャー判定を可能にする。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `want_write()` / `is_closed()` を公開しているが、ピアの SETTINGS やウィンドウサイズは取得できない
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_session_get_remote_settings`: ピアの SETTINGS
  - `nghttp2_session_get_local_settings`: ローカルの SETTINGS
  - `nghttp2_session_get_outbound_queue_size`: 送信キューサイズ
  - `nghttp2_session_get_remote_window_size`: コネクションのリモートウィンドウ残量
  - `nghttp2_session_get_local_window_size`: コネクションのローカルウィンドウ残量
  - `nghttp2_session_get_stream_remote_window_size`: ストリームのリモートウィンドウ残量
  - `nghttp2_session_get_stream_local_window_size`: ストリームのローカルウィンドウ残量
  - `nghttp2_session_check_request_allowed`: 新しいリクエストを送信できるか (ストリーム上限)
  - `nghttp2_session_get_stream_local_close`: ストリームのローカル側が閉じているか
  - `nghttp2_session_get_stream_remote_close`: ストリームのリモート側が閉じているか
  - `nghttp2_session_get_effective_recv_data_length`: フロー制御で受信可能なデータ量
  - `nghttp2_session_get_stream_effective_recv_data_length`: ストリームの受信可能データ量

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Http2Connection`)
- SETTINGS は主要フィールド (初期ウィンドウサイズ・最大同時ストリーム数・最大フレームサイズ・最大ヘッダーリストサイズ) を辞書で公開する
- ウィンドウサイズと受信可能データ量は int で公開し、ストリーム ID 引数のメソッドは存在しないストリームで `None` を返す
- `check_request_allowed` は bool で公開する
- WebTransport over HTTP/2 (`H2Session`) には追加しない (ピックアップ対象はプレーン HTTP/2 のセッション管理)
- `src/webtransport/http2.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からピア / ローカルの SETTINGS が取得できる
- Python からコネクション / ストリームのウィンドウ残量と送信キューサイズが取得できる
- Python から新しいリクエストの送信可否とストリームの終了状態が取得できる
- モックなしのテストで、各 API が動作することを確認する
