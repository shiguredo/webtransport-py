# http3.Connection の受信ヘッダーバッファがストリーム終了で解放されない

- Created: 2026-09-15
- Completed: 2026-09-20
- Branch: feature/fix-http3-pending-headers-leak
- Polished: 2026-09-15

## 目的

`src/bindings/http3.cpp` の `Http3Connection` が持つ `pending_headers_` は、ヘッダーブロックの受信途中でストリームが終了すると解放されない。1 エントリの保持量は 1 ストリームの受信窓 (既定 256 KiB) で押さえられ、同時エントリ数も広告した同時ストリーム数 (既定 100) で押さえられるが、エントリは接続終了まで解放されず、ストリームを張り替えるたびに積み上がる。

## 現状

- `pending_headers_` へ追記するのは `Http3Connection::begin_headers_cb`、`Http3Connection::begin_trailers_cb`、`Http3Connection::recv_header_cb` で、ヘッダーブロックの受信が完了するまでエントリが残る (`recv_trailer_cb` は `recv_header_cb` へ委譲する)
- 削除するのは `Http3Connection::end_headers_cb` と `Http3Connection::end_trailers_cb` のみで、`Http3Connection::stream_close_cb`、`Http3Connection::reset_stream_cb`、`Http3Connection::reset_stream` はいずれも `pending_headers_` に触れていない
- `nghttp3` のストリーム終了コールバック (`Http3Connection::stream_close_cb`) は、双方向リクエストストリームではアプリが `nghttp3_conn_close_stream` を呼んだとき、つまり `Http3Connection.close_stream` を通したときにしか発火しない (種別確定前に閉じた単方向ストリームでも発火するが、そちらにヘッダーブロックのエントリは無い)。ヘッダー受信の途中でピアが `RESET_STREAM` を送り、アプリが応答として `Client.reset_stream` / `Server.reset_stream` を呼んだ場合は `nghttp3_conn_shutdown_stream_read` が呼ばれるだけで、ストリームは削除されない
- ヘッダー受信の途中でピアが FIN を送った場合は `nghttp3` が H3_FRAME_ERROR を返して低レベル接続が閉じる (`closed_`)。閉じた後は受信 API が入力を拒否するためエントリは増えないが、オブジェクトが破棄されるまで保持される。解放されないのは `Http3Connection.close_stream` または `Http3Connection.reset_stream` が呼ばれないままストリームが終了する場合である
- `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は、アプリが呼ぶ `Client.reset_stream` / `Server.reset_stream` の中でのみ `Http3Connection.reset_stream` を呼ぶ。ピアから受信した `STREAM_RESET` はアプリのコールバック (`on_stream_reset`) に渡すだけで nghttp3 へ自動転送しないため、アプリが応答としてリセットしない限りこの経路には到達せず、エントリは接続終了まで残る。この転送漏れ (本 issue が対象とする `pending_headers_` を実際の受信経路で解放できない状態) は `issues/0240-bug-fix-http3-peer-reset-not-forwarded.md` で扱う
- 同種のコンテナは HTTP/2 側で終了時に削除されている (`src/bindings/http2.cpp` の `Http2Connection::on_stream_close_callback` が `pending_headers_` を削除する)

## 設計方針

- `Http3Connection::reset_stream_cb`、`Http3Connection::reset_stream`、`Http3Connection::stream_close_cb` のいずれでも `pending_headers_` を削除する。`Http3Connection::reset_stream_cb` はピアのリセットではなく、`nghttp3` が内部判断でストリームを中断してアプリにリセットを要求したとき (`nghttp3_conn_abort_stream`) に呼ばれる。アプリ起点のリセットは `Http3Connection::reset_stream` が `nghttp3_conn_close_stream` を呼ばないため `Http3Connection::stream_close_cb` では解放されない。この 3 経路すべてが必要である
- `src/bindings/webtransport_h3.cpp` の `H3Session` にも同型の `pending_headers_` があり `H3Session::close_stream` と `H3Session::stream_close_cb` が削除しないが、本 issue は `Http3Connection` のみを対象とする。WebTransport over HTTP/3 の本番経路は `H3Session` 側であり、本 issue の修正ではそちらの残留は解消しない
- 保持量の上限を設けるかは本 issue では扱わない。終了時の解放を追加すればエントリは接続中に解放される

## 完了条件

- ヘッダーブロックの受信途中でストリームが終了したとき、その経路 (`Http3Connection::reset_stream_cb` / `Http3Connection::reset_stream` / `Http3Connection::stream_close_cb`) に応じて `pending_headers_` のエントリが削除される
- `Http3Connection` にテスト専用の観測 API を追加する。既存のテスト専用 API は `_test_force_close` と `_has_stream_buffer` (`_test_` 接頭辞は無い) のみで `pending_headers_` の残留を観測する手段が無く、観測 API が無いと修正前後でテスト結果が変わらない
- 上記を検証するテストが追加され、全テストが通過する。`Http3Connection::reset_stream` 経路と `Http3Connection::stream_close_cb` 経路の両方を検証し、`Http3Connection::reset_stream_cb` 経路は `nghttp3` が自発的に中断する状況を作れる場合に追加する (非 2xx 応答などの経路ではヘッダー受信完了時に `Http3Connection::end_headers_cb` が先にエントリを解放するため、エントリが残る状態は再現できない)

## 解決方法

- `src/bindings/http3.cpp` の 3 経路に `pending_headers_.erase(stream_id)` を追加し、受信途中のヘッダーブロックのエントリを解放した
  - `Http3Connection::reset_stream`: `nghttp3_conn_shutdown_stream_read` は `stream_close` コールバックを呼ばないため明示的な削除が必要である。アプリ起点のリセット (`Client.reset_stream` / `Server.reset_stream`) で到達する
  - `Http3Connection::stream_close_cb`: `Http3Connection::close_stream` 経由の終了 (`nghttp3_conn_close_stream`) で発火する
  - `Http3Connection::reset_stream_cb`: nghttp3 が自発的に中断する経路 (`nghttp3_conn_abort_stream` / `nghttp3_conn_reject_stream`)。`Http3Connection` で再現できるのは GOAWAY 後の新規リクエストの拒否で、この経路はヘッダーブロックの受信開始前に中断されるためエントリは無い (非 2xx 応答も中断するが、`enable_webtransport` が無効な本クラスでは発生せず、発生する場合も `Http3Connection::end_headers_cb` が先に解放する)。この行を通るエントリはテストで再現できていない。エントリがあった場合に接続終了まで残さないための防御として呼ぶ
- `Http3Connection` にテスト専用の観測 API `Http3Connection::has_pending_headers` を追加し、`_has_pending_headers` として公開した。既存の `_has_stream_buffer` と同じ形 (エントリが無ければ `nullopt` = Python の `None`、あれば `True`) にしたのは、`begin_headers_cb` が作るエントリは最初は空で、件数では「エントリ無し」と区別できないためである
- `src/webtransport/webtransport_ext/http3.pyi` を再生成して追跡分を更新した
- `tests/test_http3.py` に 3 件のテストを追加した。HEADERS フレーム (Type 0x01) の Length を実際に渡すペイロードより大きく宣言した部分フレームを `receive_stream_data` に渡して `Http3Connection::begin_headers_cb` を発火させ、`Http3Connection::reset_stream` 経路 / `Http3Connection::close_stream` (= `Http3Connection::stream_close_cb`) 経路 / ストリーム間の独立を検証する。3 件とも解放を外した実装で失敗することを確認した
- `src/bindings/webtransport_h3.cpp` の `H3Session` にも同型の `pending_headers_` があるが、本 issue の対象外として変更していない (0231 で扱う)。高レベル層のリセット転送漏れは 0240 で扱う
- 一次資料: リクエストのキャンセル / 拒否は RFC 9114 Section 4.1.1 (キャンセル時に「resets the sending parts of streams and aborts reading on the receiving parts of streams」)。双方向リクエストストリームで `stream_close` コールバックが `nghttp3_conn_close_stream` 経由で発火することと、`reset_stream` コールバックが `nghttp3_conn_abort_stream` / `nghttp3_conn_reject_stream` で発火することは nghttp3 の実装 (`nghttp3_conn.c`) を出典とする (RFC 9114 は `refs/h3/rfc9114.txt` にある)
