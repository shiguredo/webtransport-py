# h2 の未消費受信バイト記録がストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-unconsumed-recv-bytes-leak
- Polished: 2026-09-18

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `unconsumed_recv_bytes_` は、ストリームが終了しても解放されない。長命な HTTP/2 コネクションで多数のストリームを処理するとエントリが単調増加する。あわせて、アプリへ配送せず破棄したバイトの分だけ HTTP/2 コネクションレベル受信ウィンドウが戻らない。

## 現状

- `unconsumed_recv_bytes_` へ加算するのは `H2Session::on_data_chunk_recv_callback` のみ
- 減算と削除を行うのは `H2Session::consume_recv_bytes` のみで、削除は残量が 0 になったときに限られる。`H2Session::consume_recv_bytes` は `nghttp2_session_consume` を呼び、これはコネクションとストリームの両方の `WINDOW_UPDATE` を積む
- `H2Session::initialize` は `nghttp2_option_set_no_auto_window_update` で自動 `WINDOW_UPDATE` を無効化しているため、受信ウィンドウは `H2Session::consume_recv_bytes` を呼んだ分だけしか戻らない (nghttp2 が自ら無視する DATA は例外で、nghttp2 が即時消費として扱いコネクションレベルウィンドウを自動で戻す)
- `H2Session::on_stream_close_callback` は `http2_stream_buffers_` と `end_stream_pending_` を削除するが、`unconsumed_recv_bytes_` には触れていない
- エントリが残る経路は 5 つある
  - 受理前の楽観的カプセルを上限超過で 413 拒否する経路 (`H2Session::reject_session` を呼ぶ分岐) は、加算後に `wt_sessions_` のエントリを消すため後続チャンクは早期 return し、記録だけが残る。`H2Session::reject_session` は `nghttp2_session_send` を呼ばず、クライアントも `RST_STREAM` を送らないためストリームは閉じず、`H2Session::on_stream_close_callback` も到達しない
  - クライアント側で 2xx 応答前に受信したデータは消費されない。非 2xx 応答の受信分岐も `wt_sessions_` のエントリを消すため、以後の削除経路がすべてエントリ不在で塞がれる
  - WebTransport セッション終了 (`H2Session::handle_end_stream` / `H2Session::handle_wt_close_session`) も `wt_sessions_` のエントリを消すため、未完成カプセルの分がエントリ不在のまま残る。`H2Session::handle_end_stream` は自側の END_STREAM を送らないため、ストリームは half-closed (remote) のまま接続終了まで残る
  - 受信の途中でストリームがリセットされた場合、未完成カプセルの分が残る
  - ローカル `H2Session::close_session` 後は `is_terminated` が立ち、`H2Session::on_data_chunk_recv_callback` の早期 return により `H2Session::consume_recv_bytes` の経路が塞がる。`wt_sessions_` のエントリは残るため `H2Session::on_stream_close_callback` による削除も期待できるが、これは両ハーフ終端まで到達せず、ピアが END_STREAM を返さなければ記録が接続終了まで残る
- 破棄したバイトはアプリへ配送されないため `H2Session::consume_recv_bytes` で返す機会が無く、コネクションレベル受信ウィンドウを消費したままになる。`H2SessionConfig` にコネクションレベルウィンドウの設定は無く nghttp2 の既定 65535 で固定である (`initial_window_size` は `SETTINGS_INITIAL_WINDOW_SIZE`、つまりストリーム単位のウィンドウである)。失う量は拒否したストリームで受信済みのバイト数 (少なくとも `wt_pre_accept_buffer_limit` + 1) であり、32768 バイト固定ではない
- nghttp2 が `WINDOW_UPDATE` を積むのは返却量が受信ウィンドウの半分 (65535 / 2 = 32767) 以上のときだけである。漏れの合計を X とすると返却できる量の上限は 65535 - X なので、X が 32768 バイトを超える (32769 バイト以上になる) と閾値に届かなくなり、`WINDOW_UPDATE` を二度と送出できず、アプリが消費し続けても接続が恒久的に停止する。拒否の回数ではなく漏れの合計量で決まる (X = 32768 では上限がちょうど 32767 になるため送出でき、恒久停止は保証されない)
- 既定設定では 413 拒否に到達しない。受理前ストリームが受け取れるのはストリーム受信ウィンドウの 65535 バイトまでであるのに対し、拒否条件は `capsule_buffer.size() + len > wt_pre_accept_buffer_limit` (既定 65536) であり、上限を超えられない。`initial_window_size` を大きくしてもコネクションレベルウィンドウの 65535 バイトで頭打ちになるため、再現には `wt_pre_accept_buffer_limit` を 65534 以下に下げる必要がある (既存テストは 10)
- 上限を 32768 に下げても 2 回目の拒否は起きない。1 回目の拒否で 32768 バイト超が失われ、残りのコネクションウィンドウが 32768 バイトを下回るため、2 本目は 413 に到達する前にフロー制御で停止する
- エントリ不在または `is_terminated` で早期 return する経路 (413 拒否後に届くチャンク、ローカル `close_session` 後の着信) は、記録を残さないだけでバイトは同じく破棄しているため、コネクションレベル受信ウィンドウは消費されたままになる。nghttp2 上で閉じたストリームへ届いた DATA はこれに該当しない (nghttp2 が無視した DATA を即時消費として扱い、コネクションレベルウィンドウを自動で戻す)
- 同種のストリーム単位コンテナは終了時に確実に削除されている (`src/bindings/quic.cpp` の `QuicConnection::stream_close_cb` がストリーム終了時に `stream_unacked_` と `stream_acked_offset_` を削除する。`stream_reset_cb` はイベントを積むだけで削除しないが、リセット後の両方向シャットダウンで `stream_close_cb` が呼ばれる)

## 設計方針

- ストリームが終了する経路で `unconsumed_recv_bytes_` のエントリを削除する。対象は `H2Session::on_stream_close_callback`、`H2Session::handle_end_stream`、`H2Session::handle_wt_close_session`、`H2Session::reject_session`、`H2Session::close_session`、および非 2xx 応答を受信する分岐である。`H2Session::close_session` を含めるのは、`is_terminated` が立つと `H2Session::on_data_chunk_recv_callback` の早期 return により `H2Session::consume_recv_bytes` の経路が塞がり、かつ `H2Session::on_stream_close_callback` は両ハーフ終端まで到達しない (ピアが END_STREAM を返さなければ接続終了まで残る) ためである。エントリ自体は `is_terminated` の用途で残す設計を変えず、`unconsumed_recv_bytes_` の該当キーだけを削除する
- 破棄するバイトのコネクションレベル受信ウィンドウは `nghttp2_session_consume_connection` で返す。`nghttp2_session_consume` はコネクションとストリームの両方を消費する。413 拒否の応答は END_STREAM 付きで送出されるが half-closed (local) であり受信は継続でき、nghttp2 はストリームを保持したままである。そのため `nghttp2_session_consume` を使うと拒否・終了するストリームの受信ウィンドウまで開いてしまう (返却量が受信ウィンドウの半分以上ならストリームの `WINDOW_UPDATE` も積まれる)。コネクションレベルだけを返す `nghttp2_session_consume_connection` を使う。返す対象は次の 2 つである
  - エントリ削除時に残っている `unconsumed_recv_bytes_` の残量
  - エントリ不在または `is_terminated` で早期 return する経路で破棄するバイト
- 本 issue は `unconsumed_recv_bytes_` と受信ウィンドウのみを対象とする。他のストリーム単位コンテナ (`http2_stream_buffers_` / `end_stream_pending_`) は終了時に削除済みで対象外とする。`pending_headers_` が trailer と 1xx 後の最終応答で解放されない同種の漏れは別 issue とする (h3 側の同種の漏れは別 issue で扱う)

## 完了条件

- ストリーム終了 (`H2Session::on_stream_close_callback`)、WebTransport セッション終了 (`H2Session::handle_end_stream` / `H2Session::handle_wt_close_session`)、ローカル終了 (`H2Session::close_session`)、413 拒否 (`H2Session::reject_session`)、非 2xx 応答の受信のいずれの経路でも `unconsumed_recv_bytes_` のエントリが残らない
- 拒否と破棄で消費したままのバイト数について、コネクションレベル受信ウィンドウが `nghttp2_session_consume_connection` で返る。観測は `Session.send()` が返す生フレームに `WINDOW_UPDATE` (stream 0) が現れることで行う (h2 のイベント API に `WINDOW_UPDATE` 種別は無い)。nghttp2 が `WINDOW_UPDATE` を積むのは返却量が受信ウィンドウの半分 (32767) 以上のときだけなので、拒否量が 32767 バイト以上になる設定 (例: `wt_pre_accept_buffer_limit = 32768` にして 1 ストリームへ 32769 バイト以上を流す) で検証する
- 長命コネクションで多数のストリームを処理してもエントリ数が増えないことを検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session` に、エントリ削除とコネクションレベル返却をまとめて行うヘルパーを追加し、`H2Session::on_stream_close_callback`、`H2Session::handle_end_stream`、`H2Session::handle_wt_close_session`、`H2Session::reject_session`、`H2Session::close_session`、非 2xx 応答の受信分岐から呼ぶ
- `H2Session::on_data_chunk_recv_callback` の早期 return 経路 (エントリ不在・`is_terminated`) でも、破棄するバイトを `nghttp2_session_consume_connection` で返す
- `H2Session` にテスト専用 API を追加してエントリの残留を観測できるようにする (`_test_*` の既存例に倣う)。あわせて未返却のコネクションレベル受信バイト数を観測する API を追加する (`src/bindings/http2.cpp` の `Http2Connection::effective_recv_data_length` と同じ形。拒否を重ねても上限に張り付かないことを直接表明するために使う。`WINDOW_UPDATE` の有無は完了条件のとおり `Session.send()` の生フレームで確認する)
- `src/webtransport/webtransport_ext/h2.pyi` を再生成して追跡分を更新する
- `tests/test_webtransport_h2_recv_flow_control.py` にエントリ残留の検証を、`413` 拒否の経路は `tests/test_webtransport_h2_datagram.py` の既存 413 テストに倣って追加する
