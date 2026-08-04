# nghttp2 のセッション状態・設定確認 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h2-session-state
- Polished: 2026-08-04

## 目的

HTTP/2 セッションの SETTINGS・ウィンドウサイズ・送信キュー・ストリーム状態を Python から確認できるようにし、フロー制御の監視とバックプレッシャー判定を可能にする。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `want_write()` / `is_closed()` を公開しているが、ピアの SETTINGS やウィンドウサイズは取得できない
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_session_get_remote_settings`: ピアの SETTINGS の値 (受信前はデフォルト値)
  - `nghttp2_session_get_local_settings`: ローカルの SETTINGS の値 (ピアが ACK した値。ACK 前はデフォルト値)
  - `nghttp2_session_get_outbound_queue_size`: 送信キューのフレーム数 (deferred DATA を含まない)
  - `nghttp2_session_get_remote_window_size`: コネクションのリモートウィンドウ残量
  - `nghttp2_session_get_local_window_size`: コネクションのローカルウィンドウ残量
  - `nghttp2_session_get_stream_remote_window_size`: ストリームのリモートウィンドウ残量
  - `nghttp2_session_get_stream_local_window_size`: ストリームのローカルウィンドウ残量
  - `nghttp2_session_check_request_allowed`: 新しいリクエストを送信できるか (クライアントのみ。サーバーセッション・ストリーム ID 枯渇で False)
  - `nghttp2_session_get_stream_local_close`: ストリームのローカル側が half-closed か
  - `nghttp2_session_get_stream_remote_close`: ストリームのリモート側が half-closed か
  - `nghttp2_session_get_effective_recv_data_length`: WINDOW_UPDATE を送信せずに受信した DATA ペイロードのバイト数 (受信ウィンドウの消費量)
  - `nghttp2_session_get_stream_effective_recv_data_length`: ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Connection`)。変更対象は `src/bindings/http2.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_http2_session_state.py` 等)。`src/webtransport/http2/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- Python 公開名は nghttp2 の API 名から `get_` / `check_` を除いた形とする。引数を取らない getter はプロパティ、引数を取るものはメソッドとして公開する: `remote_settings` / `local_settings` (プロパティ、dict) / `outbound_queue_size` / `remote_window_size` / `local_window_size` / `effective_recv_data_length` (プロパティ、int | None) / `request_allowed` (プロパティ、bool | None) / `stream_remote_window_size(stream_id)` / `stream_local_window_size(stream_id)` / `stream_effective_recv_data_length(stream_id)` (メソッド、int | None) / `stream_local_close(stream_id)` / `stream_remote_close(stream_id)` (メソッド、bool | None)。コネクションが閉じている場合の None 化は後述の設計方針に集約する
- SETTINGS はフロー制御の監視に必要な主要フィールド (初期ウィンドウサイズ・最大同時ストリーム数・最大フレームサイズ・最大ヘッダーリストサイズ) を辞書で公開する。辞書のキー名は `initial_window_size` / `max_concurrent_streams` / `max_frame_size` / `max_header_list_size` とし、値は nghttp2 の SETTINGS の値そのまま (受信前・ACK 前は nghttp2 のデフォルト値。initial_window_size=65535 / max_frame_size=16384 / max_header_list_size=4294967295 / max_concurrent_streams=4294967295。ただし受信前の `remote_settings` の max_concurrent_streams は nghttp2 がセッション生成時に 100 に設定し、最初の SETTINGS 受信時に 4294967295 に戻る (ピアが値を送らなかった場合のデフォルト適用))
- ウィンドウ残量と受信済みデータ量は int で公開する。ストリーム ID 引数のメソッドは、ストリームが存在しない場合に None を返す (nghttp2 は -1 を返すため。完全に閉じたストリームも nghttp2 の管理から外れて存在しなくなるため、half-closed 中は値が返り完全クローズ後に None になる)。ウィンドウ getter は負の値を返さない (ストリーム版 `local_window_size` / `remote_window_size` は SETTINGS_INITIAL_WINDOW_SIZE の縮小で内部のウィンドウが負になりうるが、nghttp2 の getter は 0 にクランプして返す。コネクション版は SETTINGS の影響を受けず、リモート側は送信時にペイロード長が残りウィンドウを超えないよう nghttp2 が制限し、ローカル側は受信ウィンドウ超過を FLOW_CONTROL_ERROR にするため負にならない)。コネクションが閉じている場合も getter は None を返す (0014 / 0016 / 0017 と同じパターン。GOAWAY 受信は既存実装が closed_ にするため閉鎖扱い。RFC 9113 の GOAWAY は graceful shutdown であり既存ストリームを継続するが、本バインディングは受信即時に閉鎖扱いとする設計。`goaway()` 送信後は閉鎖扱いにならず getter は値を返し続ける点に注意 (check_request_allowed も GOAWAY 受信のみをチェックするため、送信後も True を返す))
- `effective_recv_data_length` 系は、WINDOW_UPDATE 未送信の受信 DATA 量のため受信ウィンドウの半分を超えて変動する (nghttp2 は受信ウィンドウの半分に達すると WINDOW_UPDATE をキュー投入して 0 に戻し、送信待ちの間は蓄積が続く。ストリーム版は END_STREAM 付きの最終 DATA では WINDOW_UPDATE を送らないが、コネクション版は送る)。送信側のバックプレッシャー (送れる量) を示すものではないため、バックプレッシャー判定の主役にはならず参考値として公開する
- WebTransport over HTTP/2 (`H2Session`) には追加しない (H2Session は Http2Connection とは独立した nghttp2 セッションを管理しており、プレーン HTTP/2 のセッション監視とは目的が異なる)
- 0021 / 0022 (http2.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python からピア / ローカルの SETTINGS が取得できる (`remote_settings` / `local_settings` が辞書を返す。受信前・ACK 前のデフォルト値も確認する)
- Python からコネクション / ストリームのウィンドウ残量と送信キューのフレーム数を取得できる (`remote_window_size` 等が int を返す。存在しないストリームとコネクションが閉じている場合は None)
- Python から新しいリクエストの送信可否とストリームの half-closed 状態が取得できる (`request_allowed` が bool を返す。クライアントで True / サーバーで False になることを確認する。コネクションが閉じている場合は None。`stream_local_close` は `send_data(stream_id, data, eof=True)` の送信後に True になり (send() でフレームが送出された後)、`stream_remote_close` はピアの END_STREAM 受信後に True になることを確認する)
- Python から WINDOW_UPDATE 未送信の受信 DATA バイト数を取得できる (`effective_recv_data_length` 等が int を返す)
- モックなしのテストで、各 API が動作することを確認する (Http2Connection は低レベル受け渡し構成でテストする。クライアントとサーバーの両方の `Http2Connection` を用意して互いの送信データを受信側に流す構成は、既存の `tests/prop_http2_roundtrip.py` の `create_client_server_pair` / `exchange_settings` パターンを流用・拡張して構築する)
