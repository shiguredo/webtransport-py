# nghttp2 v1 から nghttp2-v2 への切り替えメモ

## 位置付け

- ブランチ `feature/nghttp2-v2` の作業メモである
- このブランチは当面マージしない
- 対象は HTTP/2 層 (`src/bindings/http2.*`) と WebTransport over HTTP/2 層 (`src/bindings/webtransport_h2.*`) で利用する nghttp2 を v1 (v1.70.0) から nghttp2-v2 (0.1.90) へ切り替える作業である

## 依存とビルドの変更

- `deps.json` の `nghttp2` を `https://github.com/nghttp2/nghttp2-v2` のコミット `57300274186979a3a0a40f9dc060dc20608f6528` (`ref`) に固定した
- `CMakeLists.txt` の `ExternalProject_Add(nghttp2_build ...)` に `GIT_SUBMODULES lib/sfparse` を追加した。nghttp2-v2 は Structured Field パーサ `lib/sfparse` をサブモジュールで取得し、`ENABLE_LIB_ONLY=ON` でも必須である
- インストール成果物は v1 と同じ `lib/libnghttp2.a` で、ヘッダーだけ `include/nghttp2v2/` に変わる
- nghttp2-v2 はバージョン取得関数 `nghttp2_version` と ALPN 選択関数 `nghttp2_select_alpn` を公開しない。`http2.get_version()` は `NGHTTP2_VERSION` マクロを返し、`http2.select_alpn()` はバインディング内で h2 > http/1.1 の優先順を実装した

## API の対応

| 目的 | nghttp2 v1 | nghttp2-v2 |
|---|---|---|
| 接続オブジェクト | `nghttp2_session` | `nghttp2_conn` |
| コールバック | `nghttp2_session_callbacks` (setter) | `nghttp2_callbacks` 構造体 |
| オプション | `nghttp2_option` | `nghttp2_settings` 構造体 |
| 受信 | `nghttp2_session_mem_recv2` | `nghttp2_conn_read` (戻り値は成功 0 / エラー) |
| 送信 | `nghttp2_session_mem_send2` / 送信コールバック | `nghttp2_conn_write` (アプリ提供バッファ) |
| リクエスト | `nghttp2_submit_request2` | `nghttp2_conn_submit_request` |
| レスポンス | `nghttp2_submit_response2` | `nghttp2_conn_submit_response` |
| 1xx | なし (submit_response に 1xx を渡す) | `nghttp2_conn_submit_info` |
| トレーラ | `nghttp2_submit_trailer` | `nghttp2_conn_submit_trailers` |
| RST_STREAM | `nghttp2_submit_rst_stream` | `nghttp2_conn_shutdown_stream` |
| データ供給 | `nghttp2_data_source` + read_callback | `nghttp2_data_reader.read_data` (nghttp2_vec を直接指す) |
| 送出確認 | なし | `nghttp2_write_stream_data_offset` |
| フロー制御 (受信) | 自動。`nghttp2_session_consume` で手動消費 | アプリ管理。`nghttp2_conn_extend_max_offset` / `nghttp2_conn_extend_max_stream_offset` |
| PING | `nghttp2_submit_ping` + frame callback | `nghttp2_conn_submit_ping` + `recv_ping_ack` |
| GOAWAY | `nghttp2_submit_goaway` / `terminate_session2` | `nghttp2_conn_submit_shutdown_notice` / `shutdown` / `terminate` |

## 実装上の変更

### 共通 HTTP/2 層 (`src/bindings/http2.cpp`)

- 送信は `nghttp2_conn_write` を 16384 バイトの作業バッファへ繰り返し呼び、`pending_out_` に蓄積してから返す。`want_write()` も同じ経路で判定する
- 受信は `nghttp2_conn_read` を呼び、成功時は入力長を、エラー時は 0 を返す
- nghttp2-v2 では受信ウィンドウをアプリが管理するため、v1 相当の自動 WINDOW_UPDATE (受信ウィンドウの半分で返す) を `extend_recv_connection_window` / `extend_recv_stream_window` に実装した。`local_window_size` / `effective_recv_data_length` / `stream_local_window_size` / `stream_effective_recv_data_length` はこの追跡値から返す
- `set_local_window_size` は増加を extend、減少をローカルな受信絞り込みとして v1 と同じセマンティクスで実装した
- ストリームの half-closed 状態は `stream_open` / `local_end_stream` / `remote_end_stream` / `stream_close` コールバックで追跡する。nghttp2-v2 には v1 の `nghttp2_session_get_stream_local_close` に相当する API が無い
- 1xx は `:status` が 1xx のときに `nghttp2_conn_submit_info` へ振り分ける (Python API は v1 と同じ `submit_response` のまま)
- トレーラは `nghttp2_conn_submit_trailers` を呼び、データ供給側は送信データが尽きた時点で `NGHTTP2_READ_DATA_FLAG_EOF` を返す

### WebTransport over HTTP/2 層 (`src/bindings/webtransport_h2.cpp`)

- `nghttp2_conn_write` の吸い上げを `flush_output()` に集約した。旧実装の `nghttp2_session_send` 呼び出し箇所を置き換えている
- カプセルの送信バッファを `H2StreamData` (`data` + `offset`) に変更し、`read_data` がバッファを直接指して `write_stream_data_offset` で送出確認する nghttp2-v2 の契約に対応した。END_STREAM を保留している場合は最後のチャンクと同じ呼び出しで EOF を返す (空 DATA フレームで END_STREAM を送らない)
- 受信ウィンドウはアプリ管理のため、`consume_recv_bytes` は `nghttp2_conn_extend_max_stream_offset` + `nghttp2_conn_extend_max_offset` を呼ぶ。`test_effective_recv_data_length` は v1 相当の未返却量を自前で追跡して返す
- SETTINGS は `nghttp2_settings` のフィールド (MAX_CONCURRENT_STREAMS / INITIAL_WINDOW_SIZE / ENABLE_CONNECT_PROTOCOL) と `extra_settings` (WebTransport 固有 SETTINGS、クライアントの SETTINGS_ENABLE_CONNECT_PROTOCOL) で送る。リクエスト / レスポンス処理は `begin_headers` / `recv_header` / `end_headers` (トレーラは `begin_trailers` / `recv_trailer` / `end_trailers`) と `remote_end_stream` で行う

## nghttp2-v2 で公開されない機能・観測性

以下は v1 から削除されており、バインディングの API からも削除した。

- `http2.EventType.WINDOW_UPDATE` (受信 WINDOW_UPDATE の通知が無い)
- `http2.EventType.PUSH_PROMISE` と `Connection.submit_push_promise` (Server Push が削除された)
- `http2.EventType.PRIORITY_UPDATE` (受信は内部のスケジューリングに適用されるだけで通知が無い)
- `Connection.outbound_queue_size` (送信キューのフレーム数を公開しない)
- `Connection.remote_window_size` / `Connection.stream_remote_window_size` (送信側ウィンドウを公開しない)
- 受信 PING のイベント (自動 ACK のみ。`http2.EventType.PING` は ACK の受信時だけ届き、`ping_rtt_ns` を持つ)

## v1 と挙動が変わった点

- GOAWAY は接続に 1 つだけ予約される。`goaway()` の直後に `terminate_session()` を呼んでも 2 枚にはならない
- `terminate_session(error_code, last_stream_id)` の `last_stream_id` は反映されない (ライブラリが処理済みの値を使う)。パリティ検証だけ v1 互換で残している
- `goaway(error_code)` で 0 以外のエラーコードは graceful shutdown で送れないため即時終了にフォールバックする
- `SETTINGS_MAX_FRAME_SIZE` は 16384 固定、`SETTINGS_MAX_HEADER_LIST_SIZE` は送信されない (ローカルでは無制限)。`Config.max_frame_size` / `Config.max_header_list_size` は動作に影響しない
- `Config.no_rfc7540_priorities` は無効である (nghttp2-v2 は常に `SETTINGS_NO_RFC7540_PRIORITIES=1` を送る)
- ローカルの SETTINGS は ACK の適用状態を公開しないため、ACK 前後で同じ値を返す
- CONNECT リクエストへの trailer と不正なヘッダー名 (大文字など) は接続エラーになる (v1 はストリーム単位で閉じていた)
- 1xx 中間応答の後の最終応答も通知されるため、v1 では捕捉できなかった 1xx 後の確立・拒否を処理できる (改善)
- 応答の送出失敗 (v1 の `on_frame_not_send`) は通知されない。重複応答などは submit 時のエラーイベントとして観測する

## テストの適応

- 全テスト (1464 件) が通る
- v1 の内部状態に依存していたテストは v2 で観測可能な範囲に書き換えた。主な変更は次のファイルである
  - `tests/test_http2.py`: WINDOW_UPDATE イベントと受信 PING の検証を削除し、フロー制御の再開放をデータ到達で検証
  - `tests/test_http2_session_state.py` / `tests/test_http2_session_control.py`: 設定値・ウィンドウ・GOAWAY の期待値を v2 に合わせた
  - `tests/test_http2_message_ext.py`: Server Push を削除し、PRIORITY_UPDATE は送信側だけで検証
  - `tests/test_webtransport_h2_pending_headers.py`: CONNECT への trailer / 不正ヘッダー名を接続エラーとして検証
  - `tests/test_webtransport_h2_reject_session.py`: 失敗通知を submit 時の ERROR イベントに変更
  - `tests/test_python_input_size_limits.py`: 不正入力の receive が 0 を返して接続が閉じることを検証
  - `tests/test_bindings_callback_noexcept.py`: nghttp2-v2 の designated initializer による登録も走査するよう更新
  - `tests/test_webtransport_h2_settings_limit.py` / `tests/test_http2.py`: `initial_window_size` が INT32_MAX 超のときの abort 回避 (境界で拒否) を追加

## フォローアップ

- nghttp2-v2 は開発中 (0.1.90) であり、送信側ウィンドウや受信 WINDOW_UPDATE などの観測 API、Server Push 相当の機能が追加されたらバインディングを再検討する
- `Config.max_frame_size` / `Config.max_header_list_size` / `Config.no_rfc7540_priorities` を削除するか、上流の対応を待って意味を持たせるかを判断する
