# WebTransport データストリームのリセットで RESET_STREAM_AT を送出する

- Created: 2026-08-04
- Completed: {YYYY-MM-DD}
- Branch: feature/add-reset-stream-at
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 4.4 の MUST「WebTransport implementations MUST use the RESET_STREAM_AT frame with a Reliable Size set to at least the size of the WebTransport header when resetting a WebTransport data stream」を満たし、WT ヘッダー (ストリームタイプ + セッション ID) の確実な配信を保証する。現状は通常の RESET_STREAM しか送出しないため、リセット時に WT ヘッダーが失われるとストリームのセッション関連付けを復元できない。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::reset_stream` は `ngtcp2_conn_shutdown_stream_write(conn_, 0, stream_id, error_code)` を呼ぶ (`NGTCP2_SHUT_STREAM_FLAG_NONE`。通常の RESET_STREAM 送出)
- ngtcp2 (webtransport ブランチ) は `NGTCP2_SHUT_STREAM_FLAG_FLUSH` フラグで RESET_STREAM_AT を送出できる (in-flight のストリームデータが ACK されるまでストリームを閉じない。reliable stream reset)。このフラグは、ピアが `reset_stream_at` transport parameter で対応を広告していない場合は無視される
- `reset_stream_at` transport parameter (`ngtcp2_transport_params` の `reset_stream_at` フィールド) の設定が `src/bindings/quic.cpp` に無い
- ストリームのセッション関連付けはストリーム先頭の WT ヘッダーのみで行われ (draft-ietf-webtrans-http3-16 Section 4.4)、ヘッダーが失われるとセッション ID を復元できない。このため close_stream / reset_stream のセッション ID 復元が -1 にフォールバックするケースが残る

## 設計方針

- `QuicConnection::reset_stream` で `NGTCP2_SHUT_STREAM_FLAG_FLUSH` を渡して RESET_STREAM_AT を送出する
- `reset_stream_at` transport parameter を設定する (ピアへの対応広告。`ngtcp2_transport_params` の `reset_stream_at` フィールド。サーバー / クライアント両方で設定が必要かは ngtcp2 の挙動を確認する)
- WT ヘッダーサイズ以上の Reliable Size が保証されること (ngtcp2 が FLUSH フラグでどのように Reliable Size を決定するか) を実装時に確認する
- 変更対象は `src/bindings/quic.cpp` / `src/bindings/quic.h` (reset_stream のフラグ変更・transport parameter の設定) とテスト (`tests/test_e2e_quic.py` 等)。h3 層の `H3Session::close_stream` は変更不要の見込み (QUIC 層のリセットのみ変更)
- RESET_STREAM_AT 対応により、送信済みデータがあるストリームのリセット後にピアがセッション ID を復元できるようになる

## 完了条件

- データストリームのリセットで RESET_STREAM_AT が送出される (ピア側で受信できること、または ngtcp2 の動作として保証されることを確認する)
- 送信済みデータがあるストリームのリセット後に、ピア側のセッション ID 復元が失敗しなくなる (該当ケースが -1 にフォールバックしなくなる)
- モックなしのテストで検証できる (リセット → ピア側でセッション ID が復元できることを確認する構成。0009 の低レベル API クライアント構成を流用する)
