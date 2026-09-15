# http3.Connection の受信ヘッダーバッファがストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-pending-headers-leak
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/http3.cpp` の `Http3Connection` が持つ `pending_headers_` は、ヘッダーブロックの受信途中でストリームが閉じると解放されない。受信フロー制御の窓と同時ストリーム数の分だけピア主導で保持され続ける。

## 現状

- `pending_headers_` へ追記するのは `Http3Connection::begin_headers_cb` と `Http3Connection::recv_header_cb` で、ヘッダーブロックの受信が完了するまでエントリが残る
- 削除するのはヘッダー完了時の経路のみで、`Http3Connection::stream_close_cb` は `stream_buffers_` と `shutdown_stream_ids_` だけを削除している
- `nghttp3` はストリームヘッダーを読む前に閉じたストリームを無視するため、アプリが `Http3Connection.close_stream` を呼ばなくても `stream_close_cb` は発火する。それでも `pending_headers_` は残る
- 同種のコンテナは HTTP/2 側で終了時に削除されている (`src/bindings/http2.cpp` の `Http2Connection::on_stream_close_callback` が `pending_headers_` を削除する)

## 設計方針

- `Http3Connection::stream_close_cb` に `pending_headers_` の削除を追加する
- 保持量の上限を設けるかは本 issue では扱わない。終了時の解放だけで残留は有界になる

## 完了条件

- ヘッダーブロックの受信途中でストリームが閉じたとき、`pending_headers_` のエントリが削除される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/http3.cpp` の `Http3Connection::stream_close_cb` に `pending_headers_.erase(stream_id)` を追加する
- テスト専用の観測 API を追加するか、既存の `tests/test_http3.py` の検証方法に合わせてエントリの残留を観測できるようにする
- ヘッダー受信の途中で切断する E2E を `tests/test_e2e_http3.py` に追加して、残留しないことを確認する
