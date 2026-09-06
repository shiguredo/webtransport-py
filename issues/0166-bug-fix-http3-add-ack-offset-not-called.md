# Http3Connection の get_streams_to_send が nghttp3_conn_add_ack_offset を呼ばず送信バッファがストリーム寿命まで解放されない

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-add-ack-offset-not-called
- Polished: {YYYY-MM-DD}

## 目的

`Http3Connection::get_streams_to_send` は `nghttp3_conn_add_write_offset` のみを呼び `nghttp3_conn_add_ack_offset` を呼ばない (`H3Session::get_streams_to_send` は両方呼ぶ)。`acked_stream_data_cb` が永久に到達不能で、`stream_buffers_` は `stream_close_cb` / `reset_stream_cb` まで解放されない。高レベル層は `close_stream` を一度も呼ばないため、nghttp3 のストリームオブジェクトも接続終了まで蓄積する。`read_data_cb` の O(n²) 走査も発生する。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::get_streams_to_send` は `nghttp3_conn_add_write_offset` のみを呼ぶ (grep で `nghttp3_conn_add_ack_offset(` は 0 件)
- 対照: `src/bindings/webtransport_h3.cpp` の `H3Session::get_streams_to_send` は「QUIC (ngtcp2) が再送用データを保持するため、ACK を待たずに解放してよい」として `add_ack_offset` を即呼びしている
- `Http3Connection::acked_stream_data_cb` は `nghttp3_conn_add_ack_offset` 経由でしか発火しない (nghttp3 の設計)
- 高レベル `http3/client.py` の `Client` は「`nghttp3_conn_close_stream` は大きな応答受信中に残りの DATA イベントを落とすことがあるため使わない」として `close_stream` を呼ばない
- `Http3Connection::read_data_cb` は毎回 `buffers.begin()` → end を走査し、消費済みエントリを continue でスキップするため O(n²)
- 部分 ACK 分岐 (`Http3Connection::acked_stream_data_cb` の `if (buffer.offset >= remaining)`) は到達不能かつ先頭バッファ全体を解放する不正なロジックだが、上記のとおり本コールバックは駆動されず潜在

## 設計方針

- `Http3Connection::get_streams_to_send` に `nghttp3_conn_add_ack_offset(conn_, stream_id, total)` を追加する (`H3Session::get_streams_to_send` と対称)
- `acked_stream_data_cb` の部分 ACK 分岐を `front.data.size()` ベースに直す (`H3Session::acked_stream_data_cb` と対称)
- `read_data_cb` の O(n²) は本修正で消費済みエントリが acked_stream_data_cb で pop されるようになるため解消するが、念のため確認する
- 高レベル `http3/client.py` / `http3/server.py` は QUIC の `STREAM_CLOSED` イベントで `close_stream` を呼ぶよう修正する (`nghttp3_conn_close_stream` はデータ配送が終わってから呼べば DATA 欠落は起きない)

## 完了条件

- `Http3Connection` のストリーム送信バッファが `acked_stream_data_cb` 経由で解放されること
- 長時間の HTTP/3 転送で `stream_buffers_` のサイズが有界であること
- `read_data_cb` の走査コストが O(n) になること
- `tests/` に長時間転送でのメモリ増加が有界であることを検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
