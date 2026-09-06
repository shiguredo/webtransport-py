# QUIC バインディングが ngtcp2_conn_update_pkt_tx_time を呼ばず pacing 契約に違反する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-update-pkt-tx-time-and-pacing
- Polished: {YYYY-MM-DD}

## 目的

`QuicConnection::send` は `ngtcp2_conn_writev_stream` / `ngtcp2_conn_writev_datagram` / `ngtcp2_conn_write_pkt` を呼ぶが、`ngtcp2_conn_update_pkt_tx_time` を一切呼ばない。ngtcp2 のドキュメントは「must be called after this function」と明記しており、これは契約違反。`tx.pacing.next_ts` が UINT64_MAX のまま残り pacing が恒久的に無効化される。BBR / CUBIC 等の輻輳制御が推奨する送信間隔が守られず、送信レートが cwnd 上限に対して急峻に振れる。実運用ネットワークでのバースト送信によるパケットロスの一因。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::send` は `ngtcp2_conn_writev_stream` / `ngtcp2_conn_writev_datagram` / `ngtcp2_conn_write_pkt` を呼ぶが `ngtcp2_conn_update_pkt_tx_time` を一切呼ばない (grep 済み。使用箇所は `ngtcp2_conn_get_send_quantum2` の 1 箇所のみ)
- `_deps/ngtcp2/reliable-stream-reset/source/lib/includes/ngtcp2/ngtcp2.h` の 5358-5360 行「`ngtcp2_conn_update_pkt_tx_time` must be called after this function. Application may call this function multiple times before calling `ngtcp2_conn_update_pkt_tx_time`.」
- `_deps/ngtcp2/reliable-stream-reset/source/lib/ngtcp2_conn.c` の `conn_pacing_pkt_tx_allowed` (L2247-2260) は `tx.pacing.next_ts` の初期値 UINT64_MAX (L1597) では常に許可を返す → pacing 無効
- 参考実装 `_deps/ngtcp2/reliable-stream-reset/source/examples/client.cc` の L1058、`server.cc` の L1032 は書き出しごとに `ngtcp2_conn_update_pkt_tx_time` を呼ぶ
- `ngtcp2_conn_get_expiry` は pacing 期限も含む (`ngtcp2_conn.c` L11456) ため、pacing 有効化後は `get_timeout` が pacing 期限を返すようになる

## 設計方針

- `QuicConnection::send` が正の nwrite を返した後 (可能なら Python が実際に送出した直後) に `ngtcp2_conn_update_pkt_tx_time(conn_, timestamp_ns_)` を呼ぶ
- Python 側のイベントループが pacing 期限を尊重するよう、`get_timeout` の値を使う既存のポーリング (`sleep(0.01)`) と整合させる
- 修正時の副作用注意点:
  - issue 0153 (drain 化) と組み合わせる場合、pacing で `send()` が 0 を返す頻度が上がる。drain ループの終了条件を再設計する
  - Python の `sleep(0.01)` (10 ms 粒度) と pacing 期限の相互作用を測定 (pacing 期限が 10 ms 未満のときは 10 ms 粒度に量子化される可能性)
- 実装順序: issue 0152 (`handle_timeout` 呼び出しの精緻化) → 本 issue (pacing 有効化) → issue 0153 (drain 化) の順が安全

## 完了条件

- `ngtcp2_conn_update_pkt_tx_time` が `send()` 経由で呼ばれること
- 送信レートが cwnd / RTT に応じてスムーズになること (実測)
- 既存の性能テストが劣化しないこと
- `tests/` に pacing の有効化を確認するテスト (`ngtcp2_conn_get_send_quantum2` の値が変化するか) を追加すること
- 既存のテスト全 822 件が引き続き通過すること
