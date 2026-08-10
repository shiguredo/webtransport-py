# 受信済み単方向ストリームへの書き込み登録で nghttp3 の assert が発火し得る

- Created: 2026-08-08
- Completed: 2026-08-10
- Branch: feature/fix-uni-stream-write-registration
- Polished: 2026-08-10

## 目的

`H3Session::send_stream_data` が受信済みの単方向ストリーム (クライアント起点 %4==2 / サーバー起点 %4==3) への書き込み登録を試みた場合に、nghttp3 の assert が発火してプロセスが abort し得る問題を修正する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `send_stream_data` は、`stream_info_` に登録済みかつ書き込み未登録 (`is_write_registered == false`) のストリームへの送信時に、エントリのセッション ID で `nghttp3_conn_open_wt_data_stream` を呼ぶ
- 受信済みの単方向ストリームは `recv_wt_data_cb` で `is_write_registered == false` のエントリとして `stream_info_` に登録されるため、アプリがそのストリーム ID に送信すると書き込み登録経路に乗る
- nghttp3 の `nghttp3_conn_open_wt_data_stream` はストリームの方向を assert で検査する。サーバー側は `assert(nghttp3_client_stream_bidi(stream_id) || nghttp3_server_stream_bidi(stream_id) || nghttp3_server_stream_uni(stream_id))`、クライアント側は `assert(nghttp3_client_stream_bidi(stream_id) || nghttp3_server_stream_bidi(stream_id) || nghttp3_client_stream_uni(stream_id))` を要求し、既存ストリームへの書き込み登録時はサーバー側で `assert(nghttp3_client_stream_bidi(stream_id))`、クライアント側で `assert(nghttp3_server_stream_bidi(stream_id))` を要求する。受信済み単方向ストリーム (クライアント起点 %4==2 / サーバー起点 %4==3) はこれを満たさないため、デバッグビルドで assert が発火して abort する
- リリースビルドでは assert が無効化され、不正な登録が続行される。`nghttp3_conn_open_wt_data_stream` は受信済みストリームへ WT_STREAM_UNI ヘッダ付きのフレームを送出してスケジューラに積むため、単方向ストリームへの応答データがピアへ送信され得る (単方向ストリームは送信側が 1 方向のみのため、これは不正な利用である)
- 単方向ストリームへの応答送信は本来の利用法ではないが、高レベル API は stream_id の方向性を検証しないため到達可能

## 設計方針

- `send_stream_data` の書き込み登録経路 (`is_write_registered == false` の分岐) でストリームの方向性を検証し、受信済み単方向ストリームへの書き込み登録を試みないようにする
- 方向性の判定は `stream_info_` エントリの `is_unidirectional` フィールドを使う (`recv_wt_data_cb` が設定している。クライアント起点 %4==2 とサーバー起点 %4==3 の両方を判定する)
- 受信済み単方向ストリームへの送信は黙って無視し、`is_write_registered` の更新・バッファ追加・`nghttp3_conn_resume_stream` のすべてを行わない (0027 の「未登録ストリームへの送信は無視される」と同じ扱い)。自側で `open_stream` した単方向ストリーム (`is_write_registered == true`) への送信は書き込み登録経路を通らないため、従来どおり動作する
- `send_stream_data` の docstring (`src/bindings/webtransport_h3.h` と、0027 の前例に従い `src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` の高レベル docstring) と、書き込み登録分岐のコードコメント (`src/bindings/webtransport_h3.cpp` の `send_stream_data` 内) に「受信済み単方向ストリームへの送信も無視される」旨を追記する
- 変更対象は `src/bindings/webtransport_h3.cpp` の `send_stream_data` / `src/bindings/webtransport_h3.h` / `src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` (docstring) / テスト (`tests/test_webtransport_h3_stream_buffer_cleanup.py` に追加。0027 の同種テストと同じ Sans-IO 構成を流用する。本テストは誤配送の検証が不要のため `_establish_session()` の単一セッションで足りる) / `CHANGES.md` ([FIX] エントリ追加)

## 完了条件

- 受信済み単方向ストリームへの `send_stream_data` が abort せず、黙って無視される (標準ビルドは Release (NDEBUG) のため assert は無効化されており、「abort しないこと」だけでは判別力がない。判別は送信側の `_has_stream_buffer(stream_id)` が `None` になること (旧実装では `True` のまま) を送信処理 (`_pump`) を挟む前のタイミングで確認することで行う。受信側のイベント確認は判別力を持たない。受信側 (自側で `open_stream` した単方向ストリーム) は nghttp3 の `SHUT_RD` フラグにより受信データを黙って消費するため、旧実装でも受信側イベントにはデータが現れないためである)
- 双方向ストリームの既存の送信経路は影響を受けない
- 自側で `open_stream` した単方向ストリームへの送信は従来どおり送信される (受信済み単方向ストリームへの送信だけが無視される)
- モックなしのテストで検証できる (判別テストは修正前の実装で落ちることを確認する)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `send_stream_data` の書き込み登録分岐 (`is_write_registered == false` の分岐) で、`stream_info_` エントリの `is_unidirectional` を検証し、受信済みの単方向ストリーム (クライアント起点 %4==2 / サーバー起点 %4==3) への書き込み登録を試みないようにした。受信済み単方向ストリームへの送信は黙って無視され、`is_write_registered` の更新・バッファ追加・`nghttp3_conn_resume_stream` のすべてを行わない (未登録ストリームへの送信が無視されるのと同じ扱い)。自側で `open_stream` した単方向ストリームは `is_write_registered == true` のため影響を受けない
- docstring を更新した (`src/bindings/webtransport_h3.h` と `src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` に「受信済み単方向ストリームへの送信も無視される」旨を追記)
- テストを追加した。`tests/test_webtransport_h3_stream_buffer_cleanup.py` の `test_send_to_received_uni_stream_is_ignored` で、クライアント起点 (%4==2) とサーバー起点 (%4==3) の両方向について、受信済み単方向ストリームへの送信が黙って無視され、送信バッファにエントリが残らず書き込み登録も行われないことを Sans-IO 構成で検証する
