# 受信済み単方向ストリームへの書き込み登録で nghttp3 の assert が発火し得る

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-uni-stream-write-registration
- Polished: {YYYY-MM-DD}

## 目的

`H3Session::send_stream_data` が受信済みの単方向ストリーム (クライアント起点 %4==2 / サーバー起点 %4==3) への書き込み登録を試みた場合に、nghttp3 の assert が発火してプロセスが abort し得る問題を修正する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `send_stream_data` は、`stream_info_` に登録済みかつ書き込み未登録 (`is_write_registered == false`) のストリームへの送信時に、エントリのセッション ID で `nghttp3_conn_open_wt_data_stream` を呼ぶ
- 受信済みの単方向ストリームは `recv_wt_data_cb` で `is_write_registered == false` のエントリとして `stream_info_` に登録されるため、アプリがそのストリーム ID に送信すると書き込み登録経路に乗る
- nghttp3 の `nghttp3_conn_open_wt_data_stream` は、既存ストリームへの書き込み登録時にサーバー側では `assert(nghttp3_client_stream_bidi(stream_id))` を要求する。単方向ストリーム (%4==2) はこれを満たさないため、デバッグビルドで assert が発火して abort する。リリースビルドでは assert が無効化され、不正な登録が続行される
- 単方向ストリームへの応答送信は本来の利用法ではない (単方向ストリームは送信側が 1 方向のみ) が、高レベル API は stream_id の方向性を検証しないため到達可能

## 設計方針

- `send_stream_data` の書き込み登録経路でストリームの方向性を検証し、単方向ストリームへの書き込み登録を試みないようにする
- 方向性の判定は `stream_info_` エントリの `is_unidirectional` フィールドを使う (`recv_wt_data_cb` が設定している)
- 単方向ストリームへの送信は黙って無視する (0027 の「未登録ストリームへの送信は無視される」と同じ扱い。docstring も合わせて更新する)
- 変更対象は `src/bindings/webtransport_h3.cpp` の `send_stream_data` / テスト

## 完了条件

- 受信済み単方向ストリームへの `send_stream_data` が abort せず、黙って無視される
- 双方向ストリームの既存の送信経路は影響を受けない
- モックなしのテストで検証できる
