# nghttp2 のセッション制御 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-06
- Branch: feature/add-h2-session-control
- Polished: 2026-08-04

## 目的

HTTP/2 セッションの即時切断とウィンドウサイズの動的調整を Python から行えるようにする。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `goaway(error_code)` のみを公開しており、`nghttp2_session_terminate_session2` による即時切断ができない
- ウィンドウサイズはセッション初期化時に `Http2Config.initial_window_size` の値で SETTINGS を送信するだけで、以降の動的な変更手段が無い
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_session_terminate_session2`: GOAWAY を送信して即時にセッションを終了 (last_stream_id を指定可能。送信後に want_read / want_write が 0 になる)
  - `nghttp2_session_set_local_window_size`: ローカルウィンドウサイズの動的変更 (コネクション / ストリーム。絶対値で設定)

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Connection`)。変更対象は `src/bindings/http2.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_http2_session_control.py`)。`src/webtransport/http2/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- Python 公開名は `terminate_session(error_code, last_stream_id)` / `set_local_window_size(stream_id, window_size)` とする。`set_local_window_size` は nghttp2 の API 名のまま用いる (`set_` を除くと 0020 で公開予定の `local_window_size` プロパティと衝突するため)。mutator のため成功で True / 失敗で False を返す
- `terminate_session` は `nghttp2_session_terminate_session2` をラップし、GOAWAY を送信してセッションを即時終了する (既存 `goaway()` は GOAWAY 送信後もセッションを継続する graceful shutdown。`terminate_session` は呼び出し直後から受信フレームを無視し、GOAWAY 送信後に want_read / want_write が 0 になって終了する。RFC 9113 6.8 節の graceful shutdown と即時終了の区別に相当)。`last_stream_id` はピアが開始したストリーム ID (クライアントは偶数 / サーバーは奇数、0 で省略。パリティ違反は nghttp2 が NGHTTP2_ERR_INVALID_ARGUMENT を返すため False を返す)。C++ 側の `closed_` は send() を止めてしまうため、`terminate_session` では `closed_` にせず、GOAWAY が send() でフラッシュされた後に nghttp2 の want_write が 0 になることで終了状態を確認する (is_closed() は False のまま)。コネクションが閉じている場合は no-op とし False を返す
- `set_local_window_size` は `nghttp2_session_set_local_window_size` をラップする。`stream_id` は 0 でコネクション全体、それ以外でストリーム単位。`window_size` は絶対値で指定する (delta ではない)。増加は WINDOW_UPDATE でピアへ通知されるが、減少はピアへ通知されない (ローカルでの受信絞り込みのみ。RFC 9113 6.9 節の WINDOW_UPDATE は正の増分のみを運ぶ)。負の `window_size` は C++ 側でガードし False を返す。ストリームが存在しない場合は nghttp2 が 0 (成功) を返すため True になる。コネクションが閉じている場合は no-op とし False を返す
- WebTransport over HTTP/2 (`H2Session`) には追加しない (H2Session は Http2Connection とは独立した nghttp2 セッションを管理しており、プレーン HTTP/2 のセッション制御とは目的が異なる)
- 0020 / 0022 (http2.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python から GOAWAY を送信してセッションを即時終了できる (既存 `goaway()` と異なり GOAWAY 送信後にセッションが終了状態になることを確認する。ピア側で GOAWAY 受信を確認し、終了側は `want_write()` が False になることを確認する)
- Python からコネクション / ストリームのローカルウィンドウサイズを動的に変更できる (増加時に WINDOW_UPDATE が送出され、ピア側で WindowUpdate イベントを受信してウィンドウ残量が増えることを確認する。減少時は WINDOW_UPDATE が送出されないことを確認する)
- ガード経路も確認する (パリティ違反の `last_stream_id` での False、負の `window_size` での False、コネクションが閉じている場合の False)
- モックなしのテストで、各 API が動作することを確認する (Http2Connection は低レベル受け渡し構成でテストする。クライアントとサーバーの両方の `Http2Connection` を用意して互いの送信データを受信側に流す構成は、既存の `tests/prop_http2_roundtrip.py` の `create_client_server_pair` / `exchange_settings` パターンを流用・拡張して構築する)

## 解決方法

`src/bindings/http2.cpp` / `.h` の `Http2Connection` にセッション制御メソッドを追加し、nanobind で公開した (Python 側は `webtransport.http2.Connection`)。

- `terminate_session(error_code, last_stream_id)` を追加: `nghttp2_session_terminate_session2` をラップし、GOAWAY を送信してセッションを即時終了する。呼び出し直後から受信フレームを無視し、GOAWAY 送出後に want_read / want_write が 0 になる。`closed_` にはしないため `is_closed()` は False のまま。成功で True / 失敗で False を返す
- `last_stream_id` のパリティ違反 (クライアントセッションで奇数 / サーバーセッションで偶数) と負の値は C++ 側で事前にガードする。nghttp2 に渡すと NGHTTP2_ERR_INVALID_ARGUMENT を返すが、その前にセッションの受信処理を無視状態 (IB_IGN_ALL) にしてしまうため
- `set_local_window_size(stream_id, window_size)` を追加: `nghttp2_session_set_local_window_size` をラップし、ローカルウィンドウサイズを絶対値で動的に変更する。増加時は WINDOW_UPDATE でピアへ通知され、減少時はローカルでの受信絞り込みのみ。負の `window_size` はガードして False。存在しないストリームと負の `stream_id` は nghttp2 v1.70.0 の実装で成功扱い (True)
- `goaway()` の後に `terminate_session()` を呼ぶと GOAWAY が 2 枚送信される (RFC 9113 6.8 では許容される。2 枚目の last_stream_id は既に送信した値より大きくならない)。`goaway_sent_` は `goaway()` 専用のため `terminate_session()` には影響しない

テストは `tests/test_http2_session_control.py` に追加した。クライアント・サーバーの `Http2Connection` ペアで SETTINGS 交換から検証し、terminate_session の want_write 遷移とピア側の GOAWAY 受信 (error_code / last_stream_id 含む)、パリティ違反後も通信が継続できること、ウィンドウ増加の WINDOW_UPDATE 通知とウィンドウ残量の増加、減少時の受信絞り込み (32767 バイト受信でも WINDOW_UPDATE 非送出)、ガード経路を確認する。
