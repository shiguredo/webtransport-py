# WebTransport over HTTP/2 の reset_stream に reliable_size を任意指定でき stream_id >= 2^62 で varint が壊れる

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-reset-stream-reliable-size-varint-validation
- Polished: 2026-09-07

## 目的

`H2Session::reset_stream` は Python から `reliable_size` を任意値で渡せる。draft-ietf-webtrans-http2-15 Section 6.2 は「Reliable Size MUST equal the total number of bytes the sender has sent via WT_STREAM capsules on the stream」を求めるが、実装は送信済みバイト数との一致を検証しない。加えて `encode_varint` に varint 範囲 (2^62 - 1、RFC 9000 Section 16) の検査が無く、`stream_id >= 2^62` (例: `2**63`) を渡すとワイヤが破損し別のストリームがリセットされる。`reset_stream` / `stop_sending` は未知 stream_id でも送出できる。送信側検証の欠落であり、受信側 MUST 実装との非対称でもある。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reset_stream` は `reliable_size` 引数を受け付け、`0` の場合のみ `stream_it->second.bytes_sent` にフォールバック (Python から任意値を渡せる)
- `H2Session::encode_varint` は 2^62 以上の値の検査が無く、`static_cast<uint8_t>((value >> 56) | 0xC0)` で上位ビットが壊れる
- `H2Session::reset_stream` / `H2Session::stop_sending` は存在確認を送出可否の条件にしていない (未知 ID でも `send_capsule` する)
- 実験手順 (Sans-IO の H2Session ペア。確立済みセッションで送信済みバイト数を確定させてから行う): `reset_stream(sid, st, 0, 99)` を送信済みバイト数と不一致で送るとピアが `WT_STREAM_STATE_ERROR "reliable size mismatch"` でセッションを閉じる方向であり、`reset_stream(sid, 2**63, 0)` を送るとワイヤは `c0 00 00 00 00 00 00 00` (デコード値 0) となり別ストリームを誤ってリセットする方向である。実行記録は未取得のため、実装時に再測定する
- 既存の `handle_wt_reset_stream` の受信側は `error_code > 0xffffffff` を WT_ERROR、`reliable_size != stream_info.bytes_received` を WT_STREAM_STATE_ERROR で拒否する MUST 実装は入っている (送信側と非対称)

## 設計方針

- `H2Session::reset_stream` の `reliable_size` 引数を廃止し、常に `stream_it->second.bytes_sent` を使う (送信者は自カウンタで正値を知り得るため引数は冗長。CODEBASE.md の破壊的変更許容に従う。非同期ラッパーは `reliable_size` を公開していないため影響なし)
- `H2Session::encode_varint` に 2^62 - 1 上限の検査を追加し、超過は `std::invalid_argument` を投げる (nanobind の既定翻訳で `ValueError` になる)。`stream_id` と `reliable_size` の両方に適用する (`error_code` は `uint32_t` のため対象外)
- `H2Session::reset_stream` / `stop_sending` に未知 stream_id の検査を追加し、存在しないストリームには黙って送出しない (現行の終了済みガードと同流儀。セッションは閉じない)
- `skills/webtransport-py/SKILL.md` の `reset_stream` の説明 (`def reset_stream(session_id: int, stream_id: int, error_code: int, reliable_size: int = 0) -> None` 箇所) を引数廃止に合わせて更新する
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとし、テストと `CHANGES.md` の FIX エントリを付ける。open issue 0176 の自動応答経路と 0178 の `encode_varint` 統合実装との整合は実装時に確認する

## 完了条件

- `reset_stream(sid, st, code)` が `reliable_size` 引数なしで呼べ、送信済みバイト数がワイヤに載ること
- `stream_id` / `reliable_size` に 2^62 以上を渡すと `ValueError` が発生すること
- 未知 stream_id への `reset_stream` / `stop_sending` が送出されず (黙殺)、セッションが閉じないこと
- `tests/test_webtransport_h2_reset_validation.py` を新規作成し、上記 3 経路の回帰テストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
