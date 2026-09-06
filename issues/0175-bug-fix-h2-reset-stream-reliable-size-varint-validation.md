# WebTransport over HTTP/2 の reset_stream に reliable_size を任意指定でき stream_id >= 2^62 で varint が壊れる

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-reset-stream-reliable-size-varint-validation
- Polished: {YYYY-MM-DD}

## 目的

`H2Session::reset_stream` は Python から `reliable_size` を任意値で渡せる。draft-ietf-webtrans-http2-15 Section 6.2 は「Reliable Size MUST equal the total number of bytes the sender has sent via WT_STREAM capsules on the stream」を求めるが、実装は送信済みバイト数との一致を検証しない。加えて `encode_varint` に varint 範囲 (2^62 - 1) の検査が無く、`stream_id >= 2^63` を渡すとワイヤが破損し別のストリームがリセットされる。`reset_stream` / `stop_sending` は未知 stream_id でも送出できる。仕様違反かつメモリ安全性外の不定挙動。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reset_stream` は `reliable_size` 引数を受け付け、`0` の場合のみ `stream_it->second.bytes_sent` にフォールバック (Python から任意値を渡せる)
- `H2Session::encode_varint` は 2^62 以上の値の検査が無く、`static_cast<uint8_t>((value >> 56) | 0xC0)` で上位ビットが壊れる
- `H2Session::reset_stream` / `H2Session::stop_sending` は `stream_it != wt_session->streams.end()` を確認しない (未知 ID でも `send_capsule` する)
- 実験: `reset_stream(sid, st, 0, 99)` を送信済みバイト数と不一致で送るとピアが `WT_STREAM_STATE_ERROR "reliable size mismatch"` でセッションを閉じる
- 実験: `reset_stream(sid, 2**63, 0)` を送るとワイヤは `c0 00 00 00 00 00 00 00` となりピアはストリーム 0 の STREAM_RESET を受け取る (別ストリームを誤ってリセット)
- 既存の `handle_wt_reset_stream` の受信側は `error_code > 0xffffffff` を WT_ERROR、`reliable_size != stream_info.bytes_received` を WT_STREAM_STATE_ERROR で拒否する MUST 実装は入っている (送信側と非対称)

## 設計方針

- `H2Session::reset_stream` の `reliable_size` 引数を廃止 (常に `stream_it->second.bytes_sent` を使う) するか、Python から渡された値と `bytes_sent` が一致することを検証する
- `H2Session::encode_varint` に 2^62 - 1 上限の検査を追加し、超過は例外送出 (`std::invalid_argument`)
- `H2Session::reset_stream` / `stop_sending` に未知 stream_id の検査を追加し、存在しないストリームには送出しない
- SKILL.md の `reset_stream` の説明を更新する

## 完了条件

- `reset_stream(sid, st, code)` の `reliable_size` 引数が廃止されるか、送信済みバイト数と一致することが検証されること
- `stream_id >= 2^62` を渡すと `ValueError` が発生すること
- 未知 stream_id への `reset_stream` / `stop_sending` が送出されないこと
- `tests/` に上記 3 経路の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
