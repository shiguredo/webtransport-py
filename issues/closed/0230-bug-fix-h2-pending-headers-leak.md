# h2 の受信ヘッダーバッファが trailer と 1xx 後の最終応答で解放されない

- Created: 2026-09-15
- Completed: 2026-09-20
- Branch: feature/fix-h2-pending-headers-leak
- Polished: 2026-09-18

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `pending_headers_` は、 trailer と 1xx 中間応答の後の最終応答で削除されないまま接続終了まで残る。長命な HTTP/2 コネクションでストリームを張り替えるたびにエントリとヘッダー文字列が積み上がる。

## 現状

- `pending_headers_` へ追記するのは `H2Session::on_header_callback` と `H2Session::on_begin_headers_callback` である
- 削除するのは `H2Session::on_frame_recv_callback` の `NGHTTP2_HEADERS` 分岐のうち、`NGHTTP2_HCAT_REQUEST` (サーバー側のリクエスト完了) と `NGHTTP2_HCAT_RESPONSE` (クライアント側の最初の応答完了) の 2 経路のみである
- trailer は `NGHTTP2_HCAT_HEADERS` で通知されるため、この 2 経路のどちらにも掛からず削除されない
- 1xx 中間応答の後に届く最終応答も `NGHTTP2_HCAT_HEADERS` で通知されるため削除されない。実装のコメントも「1xx を挟んだ応答の最終応答は NGHTTP2_HCAT_HEADERS で通知され、本分岐で捕捉されないため wt_sessions_ のエントリと pending_headers_ が残る (既知の制約。1xx 後の最終応答の捕捉不能)」として既知の制約としていた (修正前の記述。本対応で `pending_headers_` は解放される旨に更新済み)
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
- ストリーム終了時にも `pending_headers_` のエントリが削除される。ヘッダー単位の拒否 (不正なヘッダー名など) では `nghttp2_http_on_header` の検証が `on_header_callback` より先に走って当該ヘッダーが拒否され、nghttp2 は `on_frame_recv_callback` を発火せずに `RST_STREAM` でストリームを閉じる (`_deps/nghttp2/v1.70.0/source/lib/nghttp2_session.c` の `session_handle_invalid_stream2`。messaging 検証の失敗も同じ関数を通る) ため、拒否されたストリームではこの経路が唯一の解放になる。受信途中のまま放置して `RST_STREAM` を注入した場合は nghttp2 がストリームを閉じないため発火しない (実測)
- `pending_headers_` の残留を Python から観測するテスト専用 API を追加し (`_test_*` の既存例に倣う。戻り値は保持中のヘッダー数)、上記 2 項目を検証するテストが追加されて全テストが通過する
- 次の経路は本 issue の対象外であり、`pending_headers_` のエントリが残る (完了通知が届かないため、ストリーム終了時の解放に到達しない限り残る)
  - `NGHTTP2_HCAT_PUSH_RESPONSE` と PUSH_PROMISE の経路。本実装は SETTINGS に ENABLE_PUSH を含めないためピア起点で到達し得る (実測でエントリが残ることを確認)。別の対応とする
  - HPACK 圧縮エラーで接続を終了する経路 (nghttp2 は GOAWAY を送出するがストリームを閉じない)
- 長命コネクションで trailer を繰り返し受けても蓄積しないことの検証は、ストリーム単位の解放を確認すれば足りるため対象外とする

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` に `NGHTTP2_HCAT_HEADERS` の完了経路を追加し、`pending_headers_.erase(stream_id)` を呼ぶ (END_STREAM 付きの trailer と、1xx 中間応答の後に届く最終応答がこの分類で通知される。後者はストリームが OPENED になった後の応答であり nghttp2 は応答として検証するが、分類は `NGHTTP2_HCAT_HEADERS` になる)
- `H2Session::on_stream_close_callback` にも `pending_headers_.erase(stream_id)` を追加する。不正なヘッダー名は `nghttp2_http_on_header` の検証で `on_header_callback` より先に拒否されるため保持されないが、拒否より前に検証を通ったヘッダーは保持されたままになり、この場合は `on_frame_recv_callback` が発火しないため、この経路が唯一の解放になる
- テスト専用 API `H2Session::test_pending_header_count` を追加し、`_test_pending_header_count` として公開する (保持中のヘッダー数。エントリが無ければ `nullopt`)。`src/webtransport/webtransport_ext/h2.pyi` を再生成して追跡分を更新する
- `tests/test_webtransport_h2_pending_headers.py` を追加し、次の 3 件を検証する
  - trailer (END_STREAM 付きの `:status` を含まないヘッダーブロック) を 2 件のヘッダーに分けて分割注入し、受信途中でデコード済みの 1 件が保持されていることを表明したうえで、完了で解放されることを確認する
  - 1xx (103) の後に最終応答 (200) を注入し、最終応答の完了で解放されることを確認する (HPACK は `tests/conftest.py` の `_encode_status_header_block` を使う)
  - 検証を通るヘッダー 5 件 (疑似ヘッダー 4 件と `x-ok`) を保持させてから不正なヘッダー名 (大文字) を注入し、nghttp2 がストリームを閉じる経路で保持中の 5 件が解放されることを確認する (保持が空のエントリでも解放は観測できるが、それでは解放対象を固定したことにならないため、保持を先に表明する)
  - RED の粒度を分けて実測で確認した: `NGHTTP2_HCAT_HEADERS` 分岐の `erase` のみを外すと trailer と 1xx 後の 2 件が失敗し、`on_stream_close_callback` の `erase` のみを外すとストリーム終了の 1 件が `assert 5 is None` で失敗する (両方を外した条件だけでは、どちらの `erase` が効いているかを切り分けられない)
- `H2Session::on_frame_recv_callback` の 1xx の既知の制約コメントを、`wt_sessions_` のエントリは残るが `pending_headers_` は `NGHTTP2_HCAT_HEADERS` の分岐で解放される旨に更新する
- `tests/test_webtransport_h2_reject_session.py` の 1xx 後の最終応答の docstring から `pending_headers_` の残留を外す
- 全テストが通過する
