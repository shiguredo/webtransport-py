# h2 の受信ヘッダーバッファが trailer と 1xx 後の最終応答で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pending-headers-leak
- Polished: 2026-09-18

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `pending_headers_` は、 trailer と 1xx 中間応答の後の最終応答で削除されないまま接続終了まで残る。長命な HTTP/2 コネクションでストリームを張り替えるたびにエントリとヘッダー文字列が積み上がる。

## 現状

- `pending_headers_` へ追記するのは `H2Session::on_header_callback` と `H2Session::on_begin_headers_callback` である
- 削除するのは `H2Session::on_frame_recv_callback` の `NGHTTP2_HEADERS` 分岐のうち、`NGHTTP2_HCAT_REQUEST` (サーバー側のリクエスト完了) と `NGHTTP2_HCAT_RESPONSE` (クライアント側の最初の応答完了) の 2 経路のみである
- trailer は `NGHTTP2_HCAT_HEADERS` で通知されるため、この 2 経路のどちらにも掛からず削除されない
- 1xx 中間応答の後に届く最終応答も `NGHTTP2_HCAT_HEADERS` で通知されるため削除されない。実装のコメントも「1xx を挟んだ応答の最終応答は NGHTTP2_HCAT_HEADERS で通知され、本分岐で捕捉されないため wt_sessions_ のエントリと pending_headers_ が残る (既知の制約。1xx 後の最終応答の捕捉不能)」として既知の制約としている
- `H2Session::on_stream_close_callback` は `http2_stream_buffers_` と `end_stream_pending_` を削除するが `pending_headers_` には触れていない。そのため接続を維持したままストリームが終了しても解放されない
- trailer はピアが任意のストリームへ送信でき、本実装は拒否しない (nghttp2 は trailer を `NGHTTP2_HCAT_HEADERS` として通知し、`H2Session::on_header_callback` が `pending_headers_` へ追記する)。ピア主導でこの状態を作れる
- 1xx の挙動は `tests/test_webtransport_h2_reject_session.py` の `test_client_receive_1xx_keeps_session` と `test_client_receive_1xx_then_final_response_keeps_session` が設計ピンとして固定している。後者の docstring は「`wt_sessions_` のエントリと `pending_headers_` が残る」と書くが、表明しているのは `wt_sessions_` の残留 (DATAGRAM capsule が送出され続けること) だけであり、本 issue の修正後も表明は成立する

## 設計方針

- `H2Session::on_stream_close_callback` に `pending_headers_` の削除を追加し、ストリーム終了時に必ず解放する
- `NGHTTP2_HCAT_HEADERS` の完了時にも削除し、ストリームが閉じない経路 (サーバー側が閉じない半開きのまま残る既知の制約) でも解放する
- 本 issue は `pending_headers_` の解放のみを対象とする。`wt_sessions_` の残留 (1xx 後の最終応答の捕捉不能) は別の対応とする
- `H2Session::wt_sessions_` の挙動は変えない。既存の設計ピン 2 件の表明は `wt_sessions_` の残留のみを見ているため維持し、`pending_headers_` の残留を説明している docstring と実装コメント (`H2Session::on_frame_recv_callback` の 1xx の既知の制約コメント) だけを実態に合わせて更新する

## 完了条件

- trailer を受信したストリームと、1xx 中間応答の後に最終応答を受信したストリームで `pending_headers_` のエントリが削除される
- ストリーム終了時にも `pending_headers_` のエントリが削除される
- `pending_headers_` の残留を Python から観測するテスト専用 API を追加し (`_test_*` の既存例に倣う)、上記 2 項目を検証するテストが追加されて全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_stream_close_callback` と `H2Session::on_frame_recv_callback` の `NGHTTP2_HCAT_HEADERS` の完了経路に `pending_headers_.erase(stream_id)` を追加する
- `H2Session` にテスト専用の観測 API を追加して `pending_headers_` の残留を観測できるようにする (`_test_*` の既存例に倣う)
- `src/webtransport/webtransport_ext/h2.pyi` を再生成して追跡分を更新する
- `tests/` に trailer と 1xx 後の最終応答を注入するテストを追加する。1xx は公開 API に送出手段が無いため、`tests/test_webtransport_h2_reject_session.py` の `_encode_status_headers` と同じ HEADERS フレームのワイヤ注入で、最終応答より先に 1xx を届ける。trailer は `:status` を含まないヘッダーブロックを同じ形式で注入する。`on_frame_recv_callback` に届くのは END_STREAM を付けた trailer だけで (END_STREAM の無い trailer は nghttp2 の HTTP messaging 検証で拒否され、RST_STREAM の送出でストリームが閉じる経路になる)、完了条件 1 は END_STREAM 付きで検証する
- `H2Session::on_frame_recv_callback` の 1xx の既知の制約コメント (「`wt_sessions_` のエントリと `pending_headers_` が残る」) を、本修正で `pending_headers_` は削除される旨に更新する
