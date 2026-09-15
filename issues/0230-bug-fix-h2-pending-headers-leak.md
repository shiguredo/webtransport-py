# h2 の受信ヘッダーバッファが trailer と 1xx 後の最終応答で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pending-headers-leak
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `pending_headers_` は、 trailer と 1xx 中間応答の後の最終応答で削除されないまま接続終了まで残る。長命な HTTP/2 コネクションでストリームを張り替えるたびにエントリとヘッダー文字列が積み上がる。

## 現状

- `pending_headers_` へ追記するのは `H2Session::on_header_callback` と `H2Session::on_begin_headers_callback` である
- 削除するのは `H2Session::on_frame_recv_callback` の `NGHTTP2_HEADERS` 分岐のうち、`NGHTTP2_HCAT_REQUEST` (サーバー側のリクエスト完了) と `NGHTTP2_HCAT_RESPONSE` (クライアント側の最初の応答完了) の 2 経路のみである
- trailer は `NGHTTP2_HCAT_HEADERS` で通知されるため、この 2 経路のどちらにも掛からず削除されない
- 1xx 中間応答の後に届く最終応答も `NGHTTP2_HCAT_HEADERS` で通知されるため削除されない。実装のコメントも「1xx を挟んだ応答の最終応答は NGHTTP2_HCAT_HEADERS で通知され、本分岐で捕捉されないため wt_sessions_ のエントリと pending_headers_ が残る (既知の制約。1xx 後の最終応答の捕捉不能)」として既知の制約としている
- `H2Session::on_stream_close_callback` は `http2_stream_buffers_` と `end_stream_pending_` を削除するが `pending_headers_` には触れていない。そのため接続を維持したままストリームが終了しても解放されない
- HTTP/2 の trailer は仕様上どのストリームにも送信できるため、ピア主導でこの状態を作れる

## 設計方針

- `H2Session::on_stream_close_callback` に `pending_headers_` の削除を追加し、ストリーム終了時に必ず解放する
- `NGHTTP2_HCAT_HEADERS` の完了時にも削除し、ストリームが閉じない経路 (サーバー側が閉じない半開きのまま残る既知の制約) でも解放する
- 本 issue は `pending_headers_` の解放のみを対象とする。`wt_sessions_` の残留 (1xx 後の最終応答の捕捉不能) は別の対応とする

## 完了条件

- trailer を受信したストリームと、1xx 中間応答の後に最終応答を受信したストリームで `pending_headers_` のエントリが削除される
- ストリーム終了時にも `pending_headers_` のエントリが削除される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_stream_close_callback` と `H2Session::on_frame_recv_callback` の `NGHTTP2_HCAT_HEADERS` の完了経路に `pending_headers_.erase(stream_id)` を追加する
- `H2Session` にテスト専用の観測 API を追加して `pending_headers_` の残留を観測できるようにする (`_test_*` の既存例に倣う)
- `src/webtransport/webtransport_ext/h2.pyi` を再生成して追跡分を更新する
- `tests/` に trailer と 1xx 後の最終応答を注入するテストを追加する (不正・非コンプライアントなカプセルはワイヤ注入で再現する既存の方式に倣う)
