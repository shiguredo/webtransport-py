# WebTransport over HTTP/3 の受信ヘッダーバッファがストリーム終了で解放されない

- Created: 2026-09-15
- Completed: 2026-09-20
- Branch: feature/fix-h3-pending-headers-leak
- Polished: 2026-09-18

## 目的

`src/bindings/webtransport_h3.cpp` の `H3Session` が持つ `pending_headers_` は、QPACK デコードブロック中のストリームが終了しても解放されない。WebTransport over HTTP/3 の本番経路は `H3Session` であり、この残留はアプリの利用経路で発生する。

## 現状

- `H3Session::pending_headers_` は `H3Session::begin_headers_cb` で作られ、削除は `H3Session::end_headers_cb` の 2 箇所 (Origin 検証失敗による 403 拒否分岐と通常完了) のみである。`H3Session` は trailers コールバック (`begin_trailers` / `recv_trailer` / `end_trailers`) を登録していないため、`Http3Connection::end_trailers_cb` に相当する経路は存在しない
- `H3Session::stream_close_cb` は `forget_pre_accept_stream` と `stream_info_` の削除だけを行い `pending_headers_` に触れていない。`H3Session::close_stream` は `pending_qpack_blocked_fin_stream_ids_` と `headers_guards_` を削除するが `pending_headers_` は削除しない
- QPACK デコードブロック中は `begin_headers_cb` が発火済みで `end_headers_cb` が未発火のまま `pending_headers_` にエントリが残る (実装のコメントが「`pending_headers_` に含まれる (begin_headers_cb 発火済み・end_headers_cb 未発火)」としている状態である。同じ箇所の「ブロック中にリセットされたストリームの記録は close_stream で除去する」は保留 FIN 記録 `pending_qpack_blocked_fin_stream_ids_` の話であり、`pending_headers_` は close_stream では消えない)
- この状態でピアが `STREAM_RESET` を送ると `src/webtransport/h3/server.py` が `webtransport_session.close_stream` を呼ぶため、`pending_headers_` のエントリが接続終了まで残る
- `src/bindings/http3.cpp` の `Http3Connection::pending_headers_` にも同型の残留があるが、対象が別コンテナのため別 issue で扱う

## 設計方針

- `H3Session::close_stream` に `pending_headers_` の削除を追加する。ピア起点の `STREAM_RESET` は `src/webtransport/h3/server.py` と `src/webtransport/h3/client.py` から `close_stream` を呼ぶため、報告された残留はこの 1 箇所で解消する
- `H3Session::stream_close_cb` には削除を置かない。`close_stream` は `nghttp3_conn_close_stream` を呼び、nghttp3 が保持するストリームに対しては `stream_close_cb` を同期発火させる (`_deps/nghttp3/*/source/lib/nghttp3_conn.c` の `nghttp3_conn_close_stream2` → `conn_delete_stream` が `stream_close` を呼ぶ) ため、`stream_close_cb` 側に置いても同じ削除が二重になるだけである。`close_stream` は削除を `nghttp3_conn_close_stream` の呼び出しより前に置き、コールバック経路に依存しない
- `H3Session::reset_stream_cb` にも削除を置かない。`reset_stream_cb` は `nghttp3_conn_abort_stream` からのみ発火し、その到達経路で中断されるのは `pending_headers_` のエントリを持たない WT データストリーム (`begin_headers_cb` が発火しない) か、同じ `end_headers_cb` の中でエントリが削除される CONNECT ストリーム (クライアント側の非 2xx 応答による abort は `end_headers_cb` の削除より後に同期発火するが残留しない) である。到達経路が確認できない削除を置くと、`pending_headers_` のメンバーシップを「QPACK デコードブロック中」のマーカーとして使っている 3 箇所 (`process_after_read` の保留 FIN 記録、`receive_stream_data` の全量保持判定、`flush_unblocked_held_data` の二段判定) の意味を崩す
- 本 issue は `pending_headers_` の解放のみを対象とする。保持量の上限は扱わない

## 完了条件

- QPACK デコードブロック中にストリームが終了したとき、`H3Session::pending_headers_` のエントリが削除される
- `pending_headers_` の残留を Python から観測するテスト専用 API を `H3Session` に追加し (`_has_stream_buffer` / `_has_pending_qpack_blocked_fin_stream` と同じ `_has_*` 命名に倣う)、上記を検証するテストが追加されて全テストが通過する
  - 観測 API が無いと残留は外部挙動に現れず、修正前後でテスト結果が変わらない
- 次は本 issue の対象外である (いずれも追跡 issue は未起票。issue 候補として扱う)
  - `H3Session::enforce_pre_accept_buffer_limit` は受理前バッファの上限超過時に `nghttp3_conn_close_stream` を直接呼び、`headers_guards_` のエントリを解放しない (ピアの `STREAM_RESET` で `close_stream` が呼ばれるか接続終了まで残る。留置されるのは空のエントリ 1 件で、保持量は接続あたりの開設ストリーム数に比例する)
  - `pending_pre_accept_fin_session_ids_` は `close_stream` と `close_session` で解放されない。受理前 FIN を記録した CONNECT ストリームが受理前に `STREAM_RESET` されると `accept_session` も `reject_session` も走らないため、int64 1 件が接続終了まで残る (既定の広告済みストリーム数では有界だが、`extend_max_streams_bidi` を使うアプリでは実質無界に伸びる)
  - 接続終了時に `pending_headers_` を明示 clear する経路は無い (他の per-stream コンテナと同じ扱い。高レベル層は `CONNECTION_CLOSED` で接続ごと破棄する)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` に `pending_headers_.erase(stream_id)` を追加する (既存の `pending_qpack_blocked_fin_stream_ids_.erase` / `headers_guards_.erase` と同じ位置。`nghttp3_conn_close_stream` の呼び出しより前に置く)。`nghttp3_conn_close_stream` は `conn_delete_stream` で当該ストリームを QPACK デコーダーのブロック登録から外すため、ブロック解除後も `end_headers_cb` は発火せず、この時点で除去する以外に解放の機会が無い
- `H3Session::has_pending_headers` を追加し、`_has_pending_headers` として公開する (`has_stream_buffer` / `has_pending_qpack_blocked_fin_stream` と同じ `std::optional<bool>`。エントリが存在する場合は true、無い場合は nullopt)
- `src/webtransport/webtransport_ext/h3.pyi` を再生成して追跡分を更新する
- `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` に 2 件を追加する。ピア起点の `STREAM_RESET` は低レベル `h3.Session.close_stream(stream_id, error_code)` の呼び出しとして現れるため、`test_qpack_blocked_pre_accept_fin_reset_removes_record` と同じ構成で再現する
  - `test_qpack_blocked_reset_removes_pending_headers`: `_create_qpack_blocked_setup` でブロックさせ、ヘッダー到着後に `_has_pending_headers` で保持を表明してから `close_stream` し、解放を確認する。ブロック解除後にもエントリが復活しないことも表明する
  - `test_partial_headers_reset_removes_pending_headers`: ブロックを解除してから HEADERS フレームの先頭だけを渡し、QPACK ブロック中でない受信途中のエントリも `close_stream` で解放されることを確認する (解放が QPACK ブロック中の経路に限定されていないことの回帰ピン)
- `close_stream` の `erase` のみを外したビルドでは追加した 2 件だけが失敗し (`assert True is None`)、他の 18 件は通過することを実測で確認した
- 全テストが通過する
