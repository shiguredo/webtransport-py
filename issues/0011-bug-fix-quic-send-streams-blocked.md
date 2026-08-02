# QUIC の open_stream がストリーム上限到達時に STREAMS_BLOCKED を送出しないのを修正する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/fix-quic-send-streams-blocked
- Polished: {YYYY-MM-DD}

## 目的

RFC 9000 Section 4.6 の SHOULD「An endpoint that is unable to open a new stream due to the peer's limits SHOULD send a STREAMS_BLOCKED frame」を満たす。現在はストリーム上限に到達すると `open_stream` が -1 を返すだけで、ピアはストリームが開けないことを検知できず、上限引き上げの判断ができない。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::open_stream` は、ピアのストリーム制限 (既定 100) に到達すると ngtcp2 が `NGTCP2_ERR_STREAM_ID_BLOCKED` を返し、-1 を返すだけである
- STREAMS_BLOCKED フレーム (RFC 9000 Section 19.14) の送出は行っていない
- 高レベル `Server.open_stream` の追加により、サーバーがストリーム上限に到達する経路が現実的になった

## 設計方針

- `QuicConnection::open_stream` が `NGTCP2_ERR_STREAM_ID_BLOCKED` を受け取ったときに、ngtcp2 の STREAMS_BLOCKED 送出 API を呼び出す (API の有無は使用する ngtcp2 のバージョンで確認する)
- 送出された STREAMS_BLOCKED は既存の `send` 経路で送信される

## 完了条件

- ストリーム上限到達時の `open_stream` が -1 を返し、STREAMS_BLOCKED フレームが送出される
- モックなしのテストで検証できる (ピアのストリーム制限を下げて上限に到達させ、STREAMS_BLOCKED の受信を確認する)
