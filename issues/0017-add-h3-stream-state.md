# nghttp3 のストリーム状態確認 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h3-stream-state
- Polished: {YYYY-MM-DD}

## 目的

HTTP/3 ストリームの書き込み可否・送信完了・受信状況を Python から確認できるようにする。アプリケーションが送信タイミングを制御し、フロー制御ブロックや受信フレームの状況を把握できるようにする。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session` と `src/bindings/http3.cpp` の `Http3Connection` はストリーム状態を公開しておらず、`is_closed()` 程度の確認しかできない
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_is_stream_writable2`: ストリームが書き込み可能か (存在しない・closed・フロー制御ブロック・入力データ待ち・half-closed の判定)
  - `nghttp3_conn_is_stream_flushed`: ストリームの全送信データが ACK されたか
  - `nghttp3_conn_get_frame_payload_left2`: 受信中のフレーム残量
  - `nghttp3_conn_is_drained2`: ストリームがドレイン状態か
  - `nghttp3_conn_get_stream_wt_session_id`: ストリームが属する WebTransport セッション ID
  - `nghttp3_conn_set_max_concurrent_streams`: 同時ストリーム数の動的変更
  - `nghttp3_conn_block_stream` / `nghttp3_conn_unblock_stream`: ストリームのフロー制御ブロック制御

## 設計方針

- `H3Session` と `Http3Connection` の両方にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.h3.Session` / `webtransport.http3.Http3Connection`)
- `get_stream_wt_session_id` は WebTransport 専用のため `H3Session` のみに公開する
- ストリームが存在しない場合は `None` を返す。ブロック / アンブロックと同時ストリーム数変更は失敗時に `False` を返す
- 現在 `stream_info_` で自前管理しているセッション ID 対応を `nghttp3_conn_get_stream_wt_session_id` に置き換えるかは実装時に判断する
- `src/webtransport/h3.pyi` / `http3.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からストリームの書き込み可否・送信完了 (ACK)・ドレイン状態が取得できる
- Python から受信中フレームの残量が取得できる
- Python からストリームの WebTransport セッション ID が取得できる (H3Session)
- Python から同時ストリーム数の変更とストリームのブロック / アンブロックができる
- モックなしのテストで、各 API が動作することを確認する
