# QUIC の RETRY 受信時に接続が閉じられた状態にならない

- Created: 2026-08-05
- Completed: YYYY-MM-DD
- Branch: feature/fix-quic-retry-closed-state
- Polished: {YYYY-MM-DD}

## 目的

`ngtcp2_conn_read_pkt` が `NGTCP2_ERR_RETRY` を返した場合に接続が閉じられた状態にならず、`is_closed()` が false のまま残る問題を修正する。RETRY 受信後はコネクションが実質破棄されており、以後の統計 API 等が誤った値を返し続ける。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッド内で、`NGTCP2_ERR_RETRY` の分岐は ConnectionClosed イベントを push するだけで `closed_` を立てていない
- 他の終了系エラー (`NGTCP2_ERR_DRAINING` / `NGTCP2_ERR_CLOSING` / `NGTCP2_ERR_DROP_CONN` / `NGTCP2_ERR_CRYPTO`) は `closed_ = true` を立てる
- RETRY 受信後は ngtcp2 がコネクションを破棄済みで以後の処理が無意味になるにもかかわらず、`is_closed()` が false のまま残る。接続統計 API (ngtcp2_conn_info 等) も「閉じている場合は None」の契約に反して値を返し続ける

## 設計方針

- `NGTCP2_ERR_RETRY` の分岐でも `closed_ = true` を立てる (他の終了系エラーと同じ扱い)
- 既存の ConnectionClosed イベントの push は維持する

## 完了条件

- クライアントが RETRY パケットを受信した後、`is_closed()` が true を返すこと
- RETRY 受信後は接続統計 API (ngtcp2_conn_info 等) が None を返すこと
- モックなしのテストで RETRY 受信経路を再現して確認する (サーバー側の RETRY 送出手段が無い場合は再現方法を実測で判断する)
