# QUIC の再送データ破損を修正する

- Created: 2026-09-05
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-retransmit-corruption
- Polished: 2026-09-06

## 目的

パケットロス時の QUIC 再送でストリームデータが破損する実バグを修正する。loopback ではロスが稀なため顕在化しにくいが、CI の全スイート実行時等に大容量転送テストが bytes 不一致で失敗する。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::send()` は `ngtcp2_conn_writev_stream` で消費した分を `buf.data.erase()` で即座に消す (WRITE_MORE 分岐と通常分岐の両方)
- ngtcp2 の契約 (`_deps/ngtcp2/webtransport/source/lib/includes/ngtcp2/ngtcp2.h` の `ngtcp2_conn_writev_stream` docstring) は、`*pdatalen` のデータを `acked_stream_data_offset` が示すまで intact に保つことである (`*pdatalen == -1` の非含有と空 FIN の 0 長特例を除く)。再送時は保持メモリを再読するため、早期解放すると再送バイトがずれる
- `QuicConnection::acked_stream_data_offset_cb` (`src/bindings/quic.cpp`) は空実装 (return 0 のみ) のため、受信確認による解放が行われない
- `QuicConnection::send()` 内の `NGTCP2_ERR_STREAM_SHUT_WR` / `NGTCP2_ERR_STREAM_NOT_FOUND` 時の `buffers.clear()` と `QuicConnection::close_stream` / `QuicConnection::reset_stream` 時の `stream_buffers_.erase()` も未受信確認の解放である。`stream_close` 後は ngtcp2 が閉鎖ストリームのデータに触れない契約 (`ngtcp2_acked_stream_data_offset` docstring) のため終端状態の解放自体は許容範囲だが、本 issue で判定を明記する
- 再現根拠: `tests/test_e2e_webtransport_h3.py` の `test_large_stream_payload` が CI (run 33970064230 の `test_macos 3.14t` ジョブ、2026-09-05) で不一致となった。失敗行の `assert bytes(server_buffer) == payload` で `index 16923` が `got=0xa1 want=0x1b` (`16923 % 256 = 27`、`161 - 27 = 134` のずれ)、長さは 32768 のままである。長さ不足ならタイムアウトになるはずのため、再送バイトの内容誤りを示すSans-IO ロス注入 (手順: `create_client_server_pair` と `perform_handshake` で確立し、32 KiB を送って c2s パケットを規則的に 1 回だけ落とし、`get_timeout` / `handle_timeout` で PTO を進めて内容比較する) では決定的に再現する。観測例は複数区間のずれ (例: `[0,1170)`、`[3506,4674)` 等、先頭区間のずれ量は mod 256 で 134) であり、無ドロップ対照は完全一致する。256 バイト周期のため 256 倍数のずれは検出できない (0089 の検出限界と同一)。H3 を介さない QUIC 層単体 (`quic.Connection` 同士) で再現するため、H3 層は無関係である

## 設計方針

- 書き出し時の erase をやめ、ストリームごとに acked 位置 (`std::map<int64_t, uint64_t>` 等の新設) を管理して `QuicConnection::acked_stream_data_offset_cb` で確定受信分のみ消す。コールバックは `(stream_id, offset, datalen)` で offset 昇順・重なりなしに来るため、`offset + datalen` まで前方から消す。`datalen == 0` の FIN のみ到達時は FIN 済みとして扱う
- 送出済み未受信分は保持し、再送時の再読に耐える。FIN の送出意味 (`NGTCP2_WRITE_STREAM_FLAG_FIN` の付与条件) は変えない
- `QuicConnection::stream_close_cb` 後の解放は契約上許容されるため維持する。`SHUT_WR` / `NOT_FOUND` 時の `clear()` と `close_stream` / `reset_stream` 時の `erase` は終端状態の解放として維持し、判定を `## 解決方法` に記録する
- 回帰として Sans-IO ロス注入テスト (`tests/test_quic_stream_retransmit.py` の新設。決定的ドロップパターンと内容比較) を追加する。H3 側の即時 `nghttp3_conn_add_ack_offset` (`src/bindings/webtransport_h3.cpp` の `get_streams_to_send`) は、送出バイト列が同呼び出し内で Python `bytes` へコピーされるため早期解放でも再送に影響せず、変更なしの確認に留める

## 完了条件

- Sans-IO ロス注入 (`tests/test_quic_stream_retransmit.py`。c2s 規則ドロップ、決定的パターン複数、32 KiB 内容一致) が通る
- `QuicConnection::acked_stream_data_offset_cb` が受信確認分の解放を行い、書き出し時 erase が無くなる
- `clear()` / `erase` 各経路の保持・解放判定 (`QuicConnection::send()` の SHUT_WR / NOT_FOUND 分岐、`QuicConnection::close_stream`、`QuicConnection::reset_stream`、`QuicConnection::stream_close_cb`) が `## 解決方法` に記録される
- 既存の全テストが通る

## 検証

- Sans-IO ロス注入の回帰テストが通る
- `uv run pytest tests/` を通す
- CI が通る

## 依存関係

- 0124 の `acked_stream_data_offset_cb` 削除項目とは両立しない。本 issue が当該コールバックを実装するため、0124 側で同項目を対象外化する必要がある。0124 は open のため、本 issue 着手前に 0124 の除外を確認するか、0124 より本 issue を先行させる
- 0089 と同症状の可能性があり、本 issue の完了後に 0089 の再現条件として参照する。0089 の `test_large_post_body` と本 issue の回帰テストは対象が異なるため重複ではない
- 0034 は隣接領域 (WRITE_MORE 契約) の修正だが未磨き上げであり、0089 が不完全の可能性に言及している。送出経路の前提として実装時に再確認する
- 0013 の即時解放は、送出バイト列の同期的コピーを根拠に安全性が成立している。前提説明の契約引用は本 issue の知見で更新されるが、0013 のコード変更自体は対象外とする

## 参考

- `_deps/ngtcp2/webtransport/source/lib/includes/ngtcp2/ngtcp2.h` の `ngtcp2_conn_writev_stream` docstring (保持契約) と `ngtcp2_acked_stream_data_offset` docstring (範囲・順序・FIN 特例・stream_close 後解放可)
- `refs/` に ngtcp2 の一次資料は無いため、コードコメントには上記シンボル名と契約内容を記載する
