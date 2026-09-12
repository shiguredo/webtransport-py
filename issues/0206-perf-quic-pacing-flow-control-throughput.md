# QUIC の pacing とストリーム受信フロー制御が大容量転送のスループットを制限する

- Created: 2026-09-13
- Completed: {YYYY-MM-DD}
- Polished: {YYYY-MM-DD}

## 目的

高レベル API の送信 drain 化 (issue 0153) で 4 MiB 転送は 120 秒超 (1.69 MB で TIMEOUT) から 7.65 秒 (535 KB/s) まで改善したが、32 MiB を 10 秒以内に転送するには足りない。残る律速が `send()` の pacing とストリーム受信フロー制御にあることを特定し、そこを解消する。

## 現状

- `quic.Client` から `quic.Server` へ 1 本の片方向ストリームで 32 MiB を送ると約 99 秒かかる
- `src/bindings/quic.cpp` の `QuicConnection::send` は `ngtcp2_conn_get_send_quantum2` が返す量子ごとにしかパケットを返さない。実測では 1 回の `send()` が返すのは平均 8 パケット程度 (約 9.6 KB) で、pacing が送信機会を細かく刻んでいる
- 送信中の `QuicConnection::max_stream_data_left` が 0 になる回があり、ストリーム受信フロー制御でも止まる。`max_data_left` (コネクション) は 0 にならないため、律速はストリーム単位のウィンドウ
- `QuicConfig` の既定値は `max_data = 1048576` / `max_stream_data_bidi_remote = 262144` / `max_stream_data_uni = 262144`
- 受信側 (`quic.Server`) は `_send_to` を drain 化済みだが、`_receive` は `sock_recvfrom` を 0.1 秒タイムアウトで 1 パケットずつ読む

## 設計方針

- pacing の量子と送信機会の扱いを見直す (`ngtcp2_conn_update_pkt_tx_time` の呼び出し位置と `get_timeout` の関係を確認する)
- 受信側の `_receive` を 1 パケット読みから複数パケット読みに変え、受信処理のまとめ取りで MAX_STREAM_DATA の送出間隔を詰める
- 大容量転送の性能テスト (32 MiB) を追加し、実測値を記録する

## 完了条件

- 32 MiB を 10 秒以内に転送できること
- 大容量転送の性能テストが追加されていること
- 既存のテストが引き続き通過すること

## 関連 issue

- `issues/closed/0153-perf-drain-send-pending-throughput.md` — 送信 drain 化 (本 issue の前提)
