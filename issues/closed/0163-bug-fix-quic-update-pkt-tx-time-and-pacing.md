# QUIC バインディングが ngtcp2_conn_update_pkt_tx_time を呼ばず pacing 契約に違反する

- Created: 2026-09-06
- Completed: 2026-09-09
- Branch: feature/fix-quic-update-pkt-tx-time-and-pacing
- Polished: 2026-09-07

## 目的

`QuicConnection::send` は `ngtcp2_conn_writev_stream` / `ngtcp2_conn_writev_datagram` / `ngtcp2_conn_write_pkt` を呼ぶが、`ngtcp2_conn_update_pkt_tx_time` を一切呼ばない。ngtcp2 の `writev_stream` 契約文書は更新関数の呼び出しを求めており (`ngtcp2_conn_update_pkt_tx_time` の文書も `writev_stream` の後に呼ぶと定める)、`tx.pacing.next_ts` が初期値 `UINT64_MAX` のまま残り pacing が恒久的に無効化される。輻輳制御が推奨する送信間隔が守られず、バースト送信によるパケットロスを招き得る。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::send` は `ngtcp2_conn_writev_stream` / `ngtcp2_conn_writev_datagram` / `ngtcp2_conn_write_pkt` を呼ぶが `ngtcp2_conn_update_pkt_tx_time` を一切呼ばない (grep 済み)
- `ngtcp2.h` の `writev_stream` 契約文書は更新関数の呼び出しを求め (`ngtcp2_conn_update_pkt_tx_time` の文書も `writev_stream` の後に呼ぶと定める)、datagram 経路への直接の must 記載はないが pacing の一般効果から補強する
- `ngtcp2_conn.c` の `conn_pacing_pkt_tx_allowed` は `tx.pacing.next_ts` の初期値 `UINT64_MAX` では常に許可を返すため pacing が無効である
- 参考実装の `examples/client.cc` と `server.cc` は aggregate 書き出し後に無条件で (nwrite 0 でも) 1 回呼ぶ。aggregate 系は内部で更新済みのため、本リポジトリの writev 直呼び経路とはパターンが異なる
- `ngtcp2_conn_get_expiry` は他 expiry と pacing 期限の最小値を返すため、pacing 有効化後は最小の場合に `get_timeout` が pacing 期限を返すようになる

## 設計方針

- `QuicConnection::send` の確定した書き出し (MORE 継続要求時を除く全 return 経路。nwrite の値にかかわらず無条件) の直前に `ngtcp2_conn_update_pkt_tx_time(conn_, timestamp_ns_)` を呼ぶ。MORE 使用中は他 ngtcp2 API 呼び出しが禁じられるため継続要求時には呼ばない。時刻は `send()` 入口の `timestamp_ns_` を使う (Sans-IO 構造上、Python の UDP 送出時刻を C++ 層で使う手段がないため)
- Python 層の変更は行わない。pacing 期限は既存 `get_timeout` 経路で返るようになり、10 ms ポーリング粒度で量子化される点は許容する
- 0152 / 0153 との実装順序の規定は設けない (相互の本文に順序根拠がないため)。0153 側の drain 終了条件への影響は 0153 側で扱う

## 完了条件

- `send()` の確定書き出しで `ngtcp2_conn_update_pkt_tx_time` が呼ばれること
- 大量送信直後に `get_timeout()` が未来の pacing 期限を返すこと (Sans-IO で観測する)
- `tests/test_quic_pacing.py` を新規作成し、上記 2 件を検証すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- `QuicConnection::send` の確定書き出し 4 経路の直後に `ngtcp2_conn_update_pkt_tx_time` を呼ぶ。継続要求時と未書き出し時は呼ばない
- Sans-IO 駆動の共用補助に期限待ちを入れ、既存ポンプを pacing 対応にする
- `tests/test_quic_pacing.py` に 2 件のテスト (確定書き出しの期限設定・大量送信の期限報告) を追加する
- 全 958 件のテストが通過することと、レビュー 5 周で致命的と重要が 0 件であることを確認した
