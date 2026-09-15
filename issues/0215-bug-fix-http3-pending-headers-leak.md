# http3.Connection の受信ヘッダーバッファがストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-pending-headers-leak
- Polished: 2026-09-15

## 目的

`src/bindings/http3.cpp` の `Http3Connection` が持つ `pending_headers_` は、ヘッダーブロックの受信途中でストリームが終了すると解放されない。1 エントリの保持量は 1 ストリームの受信窓 (既定 256 KiB) で押さえられ、同時エントリ数も広告した同時ストリーム数 (既定 100) で押さえられるが、エントリは接続終了まで解放されず、ストリームを張り替えるたびに積み上がる。

## 現状

- `pending_headers_` へ追記するのは `Http3Connection::begin_headers_cb`、`Http3Connection::begin_trailers_cb`、`Http3Connection::recv_header_cb` で、ヘッダーブロックの受信が完了するまでエントリが残る (`recv_trailer_cb` は `recv_header_cb` へ委譲する)
- 削除するのは `Http3Connection::end_headers_cb` と `Http3Connection::end_trailers_cb` のみで、`Http3Connection::stream_close_cb`、`Http3Connection::reset_stream_cb`、`Http3Connection::reset_stream` はいずれも `pending_headers_` に触れていない
- `nghttp3` のストリーム終了コールバック (`Http3Connection::stream_close_cb`) は、アプリが `nghttp3_conn_close_stream` を呼んだとき、つまり `Http3Connection.close_stream` を通したときにしか発火しない。ヘッダー受信の途中でピアが `RESET_STREAM` を送った場合は `nghttp3_conn_shutdown_stream_read` が呼ばれるだけで、ストリームは削除されない
- ヘッダー受信の途中でピアが FIN を送った場合は `nghttp3` が H3_FRAME_ERROR を返して接続が閉じるため、この経路ではエントリは接続ごと消える。解放されないのは `Http3Connection.close_stream` または `Http3Connection.reset_stream` が呼ばれた場合である
- `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は `Http3Connection.reset_stream` を呼ぶため、アプリ起点のリセットではこの経路に到達する
- 同種のコンテナは HTTP/2 側で終了時に削除されている (`src/bindings/http2.cpp` の `Http2Connection::on_stream_close_callback` が `pending_headers_` を削除する)

## 設計方針

- `Http3Connection::reset_stream_cb`、`Http3Connection::reset_stream`、`Http3Connection::stream_close_cb` のいずれでも `pending_headers_` を削除する。`Http3Connection::reset_stream_cb` はピアのリセットではなく、`nghttp3` が内部判断でストリームを中断してアプリにリセットを要求したとき (`nghttp3_conn_abort_stream`) に呼ばれる。アプリ起点のリセットは `Http3Connection::reset_stream` が `nghttp3_conn_close_stream` を呼ばないため `Http3Connection::stream_close_cb` では解放されない。この 3 経路すべてが必要である
- `src/bindings/webtransport_h3.cpp` の `H3Session` にも同型の `pending_headers_` があり `H3Session::close_stream` と `H3Session::stream_close_cb` が削除しないが、本 issue は `Http3Connection` のみを対象とする。WebTransport over HTTP/3 の本番経路は `H3Session` 側であり、本 issue の修正ではそちらの残留は解消しない
- 保持量の上限を設けるかは本 issue では扱わない。終了時の解放を追加すればエントリは接続中に解放される

## 完了条件

- ヘッダーブロックの受信途中でストリームが終了したとき、その経路 (`Http3Connection::reset_stream_cb` / `Http3Connection::reset_stream` / `Http3Connection::stream_close_cb`) に応じて `pending_headers_` のエントリが削除される
- `Http3Connection` にテスト専用の観測 API を追加する。既存のテスト専用 API は `_test_force_close` と `_has_stream_buffer` (`_test_` 接頭辞は無い) のみで `pending_headers_` の残留を観測する手段が無く、観測 API が無いと修正前後でテスト結果が変わらない
- 上記を検証するテストが追加され、全テストが通過する。`Http3Connection::reset_stream` 経路と `Http3Connection::stream_close_cb` 経路の両方を検証し、`Http3Connection::reset_stream_cb` 経路は `nghttp3` が自発的に中断する状況 (非 2xx 応答など) を作れる場合に追加する

## 解決方法

- `src/bindings/http3.cpp` の `Http3Connection::reset_stream_cb`、`Http3Connection::reset_stream`、`Http3Connection::stream_close_cb` に `pending_headers_.erase(stream_id)` を追加する
- `Http3Connection` に `_test_pending_header_count` のようなテスト専用の観測 API を追加し、`src/webtransport/webtransport_ext/http3.pyi` を再生成して追跡分を更新する
- `tests/test_http3.py` の低レベル構成で検証する。HEADERS の Length を実際より大きく宣言してペイロードを途中まで `receive_stream_data` に渡すと `Http3Connection::begin_headers_cb` が発火してエントリが作られる。ピアのリセット自体を注入する API は無いため、ピアの `RESET_STREAM` を受けたアプリが行う応答として `Http3Connection.reset_stream` をテストから呼び、エントリが消えることを検証する
