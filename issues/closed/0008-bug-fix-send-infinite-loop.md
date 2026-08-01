# QUIC の send() が輻輳ウィンドウ枯渇時に無限ループするバグを修正する

- Created: 2026-08-01
- Completed: 2026-08-01
- Branch: feature/fix-send-infinite-loop
- Polished: {YYYY-MM-DD}

## 目的

QUIC の送信パス (`src/bindings/quic.cpp` の `QuicConnection::send`) が、輻輳ウィンドウ (cwnd) 枯渇時に無限ループしてイベントループをブロックするバグを修正する。このバグは大容量のデータ送信時にテストがハングする原因となる。

## 現状

- `QuicConnection::send` のストリームデータ送信ループは、`ngtcp2_conn_writev_stream` が 0 を返す (パケットを書けなかった) 場合に while ループの先頭へ戻り、同じバッファで再試行を無限に繰り返す。`ngtcp2_conn_writev_datagram` のループも同様
- 0 が返るのは cwnd 枯渇 (ACK 未受信で送信枠が尽きた) などの送信不可状態で、ACK 受信まで解消されないためループを抜けられない
- `NGTCP2_ERR_WRITE_MORE` でデータを消費できなかった場合 (ndatalen == 0 / accepted == false) も同様に無限ループする
- 2MB のデータを `send_stream_data` に積んで `send` を連続呼び出しすると、2 回目の呼び出しが返らないことで再現を確認済み

## 設計方針

- ループ内で進捗が無い場合 (nwrite == 0、またはデータを消費しない WRITE_MORE) はループを抜けて、通常パケット (ACK など) の書き込みに進む
- 送信できなかったデータはバッファに残るため、次回の `send` 呼び出しで再試行される (ACK 受信後の送信機会に送られる)
- サーバー・クライアント両方の送信パスで共有されるため、修正は `send` のみ

## 完了条件

- 輻輳ウィンドウ枯渇状態で `send` が無限ループせず返る
- フロー制御枠を超えるデータ (2MB など) の送信が停止せず完了する
- モックなしの e2e テストで大容量データの送受信を検証できる

## 解決方法

- `src/bindings/quic.cpp` の `QuicConnection::send` で、ストリームデータと Datagram の送信ループがパケットを書けない場合 (nwrite == 0) に無限ループしていたのを、ループを抜けて次回の送信機会に回すように修正した
- `NGTCP2_ERR_WRITE_MORE` でデータを消費できなかった場合 (ndatalen == 0 / accepted == false) も同様にループを抜けるようにした
- 送信できなかったデータはバッファに残るため、ACK 受信後の `send` 呼び出しで再送される
- `tests/test_e2e_quic.py` に `test_cwnd_exhaustion_does_not_hang` を追加した。run() を回さずに 64KB を連続送信して cwnd を枯渇させ、その後 run() で送信が完了することを検証する
