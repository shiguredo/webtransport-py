# Http3Connection の get_streams_to_send が nghttp3_conn_add_ack_offset を呼ばず送信バッファがストリーム寿命まで解放されない

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-add-ack-offset-not-called
- Polished: 2026-09-07

## 目的

`Http3Connection::get_streams_to_send` は `nghttp3_conn_add_write_offset` のみを呼び `nghttp3_conn_add_ack_offset` を呼ばない (`H3Session::get_streams_to_send` は両方呼ぶ)。`acked_stream_data_cb` が永久に到達不能で、`stream_buffers_` は `stream_close_cb` / `reset_stream_cb` まで解放されない。高レベル層は `close_stream` を一度も呼ばないため、送信バッファがストリーム寿命まで残留する。`read_data_cb` の先頭走査も残留エントリ分だけ延びる。本 issue の対象はバインディング層の ACK 対称化のみとし、高レベル層の `close_stream` 追加は行わない (既存の DATA 欠落回避コメントを覆す根拠がないため)。closed issue 0013 は `Http3Connection` を対象外として `H3Session` のみ対応した経緯があり、本 issue はその残件を扱う。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::get_streams_to_send` は `nghttp3_conn_add_write_offset` のみを呼ぶ (grep で `nghttp3_conn_add_ack_offset(` は 0 件)
- 対照: `src/bindings/webtransport_h3.cpp` の `H3Session::get_streams_to_send` は「QUIC (ngtcp2) が再送用データを保持するため、ACK を待たずに解放してよい」として `add_ack_offset` を即呼びしている
- `Http3Connection::acked_stream_data_cb` は `nghttp3_conn_add_ack_offset` 経由でしか発火しない (nghttp3 の設計)
- 高レベル `http3/client.py` の `Client` は「`nghttp3_conn_close_stream` は大きな応答受信中に残りの DATA イベントを落とすことがあるため使わない」として `close_stream` を呼ばない
- `Http3Connection::read_data_cb` は毎回 `buffers.begin()` → end を走査し、消費済みエントリを continue でスキップするため O(n²)
- 部分 ACK 分岐 (`Http3Connection::acked_stream_data_cb` の `if (buffer.offset >= remaining)`) は到達不能かつ先頭バッファ全体を解放する不正なロジックだが、上記のとおり本コールバックは駆動されず潜在

## 設計方針

- `Http3Connection::get_streams_to_send` に `nghttp3_conn_add_ack_offset` を追加する。データあり経路は `total` で、FIN のみ経路は `0` で呼ぶ (`H3Session::get_streams_to_send` と対称)。安全性の前提は H3 側と同一であり、Http3 の送信連鎖 (`http3.cpp` の `nb::bytes` コピー → `client.py` / `server.py` の `send_stream_data` 受け渡し) も QUIC 層の再送保持に依存するため QUIC が再送用データを保持する点は変わらない
- `acked_stream_data_cb` を `H3Session::acked_stream_data_cb` と同形に書き換える (`data.size()` 比較 + 部分 ACK 時の部分 erase。`offset` フィールドの扱いも H3 に倣い無視する。変数名 `buffer` は現状維持)
- `read_data_cb` に H3 同形の空 FIN エントリ除去を追加する (データ量 0 で FIN 付きの読み出し済みエントリは `acked` 経路で解放されないため)
- `read_data_cb` の走査はコード変更せず、消費済みエントリが残留しないことを構造確認する
- 変更対象は `src/bindings/http3.cpp` と `src/bindings/http3.h` (テスト専用アクセサの追加を含む) のみとし、Python 高レベル層の変更は行わない

## 完了条件

- `Http3Connection` のストリーム送信バッファが `acked_stream_data_cb` 経由で解放されること (テスト専用アクセサで確認する)
- 長時間の HTTP/3 転送で `stream_buffers_` のエントリ数が同時送信ストリーム数以下に収まること
- 消費済みエントリが `read_data_cb` の走査に残留しないこと (構造確認)
- `tests/test_http3_ack_offset.py` を新規作成し、送信バッファ解放と空 FIN 除去を検証すること (H3 側の `test_webtransport_h3_ack_offset.py` と対称の形式)
- 既存のテスト全 834 件が引き続き通過すること
