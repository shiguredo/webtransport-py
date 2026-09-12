# nghttp2 v1.70.0 で deprecated と明記されている API 群 (ssize_t 版) を *2 版に移行する

- Created: 2026-09-07
- Completed: 2026-09-13
- Branch: feature/refactor-migrate-to-nghttp2-non-deprecated-apis
- Polished: {YYYY-MM-DD}

## 目的

`Http2Connection` と `H2Session` の両方が nghttp2 v1.70.0 で deprecated と明記されている API 群 (`nghttp2_session_mem_send` / `mem_recv` / `submit_request` / `submit_response` / `send_callback` / `data_provider` の ssize_t 版) を全面的に使用している。これらは `#ifndef NGHTTP2_NO_SSIZE_T` 配下にあり、`NGHTTP2_NO_SSIZE_T` が定義されるとビルド不能になる。nghttp2 の将来バージョンで削除される可能性が高いため、`*2` 版 (`nghttp2_ssize` を返す) に移行する。加えて `Http2Connection` の `send_callback` は登録されるが `mem_send` 使用のため死にコード (`nghttp2.h` の 3491 行「mem_send does not use nghttp2_send_callback」)。

## 現状

- `src/bindings/http2.cpp` で `nghttp2_session_callbacks_set_send_callback` (:96)、`nghttp2_session_mem_recv` (:162)、`nghttp2_session_mem_send` (:178)、`nghttp2_submit_request` (:221)、`nghttp2_submit_response` (:262)、`nghttp2_data_provider` (:217, :256)、`nghttp2_data_source_read_callback` (:905)、`nghttp2_send_callback` (:417) を使用
- `src/bindings/webtransport_h2.cpp` で同じ API を使用 (計 5 箇所)
- `_deps/nghttp2/v1.70.0/source/lib/includes/nghttp2/nghttp2.h` で上記 API に `Deprecated. Use nghttp2_*_send2 / mem_recv2` のコメントあり
- nghttp2 の examples (`_deps/nghttp2/v1.70.0/source/examples/client.c:464`、`libevent-server.c:403`) は `*2` 版を使用
- `nghttp2_select_alpn` は deprecated ではない (`nghttp2_select_next_protocol` の後継)
- `http2.cpp` の `send_callback` は `nghttp2_session_send` を一切呼ばず (`mem_send` のみ使用) 事実上死にコード

## 設計方針

- 全 API を `*2` 版に置き換える: `nghttp2_session_mem_send` → `mem_send2`、`mem_recv` → `mem_recv2`、`submit_request` → `submit_request2`、`submit_response` → `submit_response2`、`data_provider` → `data_provider2`、`data_source_read_callback` → `data_source_read_callback2`、`send_callback` → `send_callback2`
- 戻り値型を `ssize_t` から `nghttp2_ssize` に変える
- `Http2Connection` の `send_callback` (死にコード) と `send_buffer_` フィールドを削除する
- `WebTransport over HTTP/2` の `send_callback` は蓄積型 (`send_buffer_` に貯める) で使用中のため `send_callback2` に置き換えて維持する
- deprecated API 使用の警告が prek の静的解析 (`ty` は Python なので対象外だが `clang-tidy` 相当) で検出できるかを検討する

## 完了条件

- `#define NGHTTP2_NO_SSIZE_T` を明示的に設定してもビルドが通ること (CI に検証ジョブを追加)
- `Http2Connection::send_callback` と `send_buffer_` の死にコードが削除されていること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

- `src/bindings/http2.cpp` と `src/bindings/webtransport_h2.cpp` の nghttp2 API を `*2` 版へ置き換えた。置き換え対象は `nghttp2_session_mem_send2` / `nghttp2_session_mem_recv2` / `nghttp2_submit_request2` / `nghttp2_submit_response2` / `nghttp2_data_provider2` / `nghttp2_data_source_read_callback2` / `nghttp2_session_callbacks_set_send_callback2` で、戻り値型はすべて `nghttp2_ssize` にした
- `Http2Connection::send_callback` は `nghttp2_session_mem_send2` しか使わないため一度も呼ばれない死にコードだった。コールバック登録・宣言・定義と、送信に使われていなかった `send_buffer_` メンバー (ムーブコンストラクタとムーブ代入の転送を含む) を削除した
- `H2Session::send_callback` は `nghttp2_session_send` を使う蓄積型の実装で実際に呼ばれるため、`send_callback2` として維持した
- `NGHTTP2_NO_SSIZE_T` を定義した wheel ビルドを行う `build_no_ssize_t` ジョブを `.github/workflows/test.yml` に追加した。deprecated API に戻すとこのジョブがコンパイルエラーで落ちることをローカルで確認済み
