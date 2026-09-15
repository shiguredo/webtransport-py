# h2 の未消費受信バイト記録がストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-unconsumed-recv-bytes-leak
- Polished: 2026-09-15

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `unconsumed_recv_bytes_` は、ストリームが終了しても解放されない。長命な HTTP/2 コネクションで多数のストリームを処理するとエントリが単調増加する。あわせて、アプリへ配送せず破棄したバイトの分だけ HTTP/2 コネクションレベル受信ウィンドウが戻らない。

## 現状

- `unconsumed_recv_bytes_` へ加算するのは `H2Session::on_data_chunk_recv_callback` のみ
- 減算と削除を行うのは `H2Session::consume_recv_bytes` のみで、削除は残量が 0 になったときに限られる。`H2Session::consume_recv_bytes` は `nghttp2_session_consume` を呼び、これはコネクションとストリームの両方の `WINDOW_UPDATE` を積む
- `H2Session::initialize` は `nghttp2_option_set_no_auto_window_update` で自動 `WINDOW_UPDATE` を無効化しているため、受信ウィンドウは `H2Session::consume_recv_bytes` を呼んだ分だけしか戻らない
- `H2Session::on_stream_close_callback` は `http2_stream_buffers_` と `end_stream_pending_` を削除するが、`unconsumed_recv_bytes_` には触れていない
- エントリが残る経路は 3 つある
  - 受理前の楽観的カプセルを上限超過で 413 拒否する経路 (`H2Session::reject_session` を呼ぶ分岐) は、加算後に `wt_sessions_` のエントリを消すため後続チャンクは早期 return し、記録だけが残る。`H2Session::reject_session` は `nghttp2_session_send` を呼ばず、クライアントも `RST_STREAM` を送らないためストリームは閉じず、`H2Session::on_stream_close_callback` も到達しない
  - クライアント側で 2xx 応答前に受信したデータは消費されない。非 2xx 応答の受信分岐も `wt_sessions_` のエントリを消すため、以後の削除経路がすべてエントリ不在で塞がれる
  - 受信の途中でストリームがリセットされた場合、未完成カプセルの分が残る
- 破棄したバイトはアプリへ配送されないため `H2Session::consume_recv_bytes` で返す機会が無く、コネクションレベル受信ウィンドウ (既定 65535) を消費したままになる。32768 バイトの拒否が 2 回起きるだけで全ストリームが恒久的に停止する
- エントリ不在または `is_terminated` で早期 return する経路 (拒否後に届くチャンク、終了済みセッションへの着信) は、記録を残さないだけでバイトは同じく破棄しているため、コネクションレベル受信ウィンドウは消費されたままになる
- 同種のストリーム単位コンテナは終了時に確実に削除されている (`src/bindings/quic.cpp` の `QuicConnection::stream_close_cb` がストリーム終了時に `stream_unacked_` と `stream_acked_offset_` を削除する。`stream_reset_cb` はイベントを積むだけで削除しないが、リセット後の両方向シャットダウンで `stream_close_cb` が呼ばれる)

## 設計方針

- ストリームが終了する経路で `unconsumed_recv_bytes_` のエントリを削除する。対象は `H2Session::on_stream_close_callback`、`H2Session::handle_end_stream`、`H2Session::handle_wt_close_session`、`H2Session::reject_session`、および非 2xx 応答を受信する分岐である。`H2Session::close_session` はエントリを残して `is_terminated` を立てる設計であり、ストリームが閉じた時点で `H2Session::on_stream_close_callback` が削除するため追加しない
- 破棄するバイトのコネクションレベル受信ウィンドウは `nghttp2_session_consume_connection` で返す。`nghttp2_session_consume` はストリームレベルの返却も行うが、対象のストリームは終了・拒否済みで `wt_sessions_` のエントリが無く、ストリーム単位の返却は不要であるため、コネクションレベルだけを返す API を使う。返す対象は次の 2 つである
  - エントリ削除時に残っている `unconsumed_recv_bytes_` の残量
  - エントリ不在または `is_terminated` で早期 return する経路で破棄するバイト
- 本 issue は `unconsumed_recv_bytes_` と受信ウィンドウのみを対象とする。他のストリーム単位コンテナ (`http2_stream_buffers_` / `end_stream_pending_`) は終了時に削除済みで対象外とする。`pending_headers_` が trailer と 1xx 後の最終応答で解放されない同種の漏れは別 issue とする (h3 側の同種の漏れは別 issue で扱う)

## 完了条件

- ストリーム終了 (`H2Session::on_stream_close_callback`)、WebTransport セッション終了 (`H2Session::handle_end_stream` / `H2Session::handle_wt_close_session`)、413 拒否 (`H2Session::reject_session`)、非 2xx 応答の受信のいずれの経路でも `unconsumed_recv_bytes_` のエントリが残らない
- 拒否と破棄で消費したままのバイト数について、コネクションレベル受信ウィンドウが `nghttp2_session_consume_connection` で返り、設定済みのウィンドウに見合う `WINDOW_UPDATE` (stream 0) が送出される
- 長命コネクションで多数のストリームを処理してもエントリ数が増えないことを検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session` に、エントリ削除とコネクションレベル返却をまとめて行うヘルパーを追加し、`H2Session::on_stream_close_callback`、`H2Session::handle_end_stream`、`H2Session::handle_wt_close_session`、`H2Session::reject_session`、非 2xx 応答の受信分岐から呼ぶ
- `H2Session::on_data_chunk_recv_callback` の早期 return 経路 (エントリ不在・`is_terminated`) でも、破棄するバイトを `nghttp2_session_consume_connection` で返す
- `H2Session` にテスト専用 API を追加してエントリの残留を観測できるようにする (`_test_*` の既存例に倣う)。あわせてコネクションレベル受信ウィンドウを観測する API を追加する (`src/bindings/http2.cpp` の `Http2Connection::effective_recv_data_length` と同じ形)
- `src/webtransport/webtransport_ext/h2.pyi` を再生成して追跡分を更新する
- `tests/test_webtransport_h2_recv_flow_control.py` にエントリ残留の検証を、`413` 拒否の経路は `tests/test_webtransport_h2_datagram.py` の既存 413 テストに倣って追加する
