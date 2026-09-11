# WebTransport over HTTP/2 の reset_stream に reliable_size を任意指定でき stream_id >= 2^62 で varint が壊れる

- Created: 2026-09-07
- Completed: 2026-09-12
- Branch: feature/fix-h2-reset-stream-reliable-size-varint-validation
- Polished: 2026-09-09

## 目的

`H2Session::reset_stream` は Python から `reliable_size` を任意値で渡せる。draft-ietf-webtrans-http2-15 Section 6.2 は「Reliable Size MUST equal the total number of bytes the sender has sent via WT_STREAM capsules on the stream」を求めるが、実装は送信済みバイト数との一致を検証しない。加えて `encode_varint` に varint 範囲 (2^62 - 1、RFC 9000 Section 16) の検査が無く、`stream_id >= 2^62` (例: `2**63`) を渡すとワイヤが破損し別のストリームがリセットされる。`reset_stream` / `stop_sending` は未知 stream_id でも送出できる。送信側検証の欠落であり、受信側 MUST 実装との非対称でもある。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reset_stream` は `reliable_size` 引数を受け付け、`0` の場合のみ `stream_it->second.bytes_sent` にフォールバック (Python から任意値を渡せる)
- `H2Session::encode_varint` は 2^62 以上の値の検査が無く、`static_cast<uint8_t>((value >> 56) | 0xC0)` で上位ビットが壊れる
- `H2Session::reset_stream` / `H2Session::stop_sending` は存在確認を送出可否の条件にしていない (未知 ID でも `send_capsule` する)
- `reset_stream` の現行ガード順はセッション終了 → 終了済みストリーム (`ResetSent` / `DataSent`) → `encode_varint(stream_id)` であり、未知ストリーム検査は無い
- 実験手順 (Sans-IO の H2Session ペア。確立済みセッションで送信済みバイト数を確定させてから行う): `reset_stream(sid, st, 0, 99)` を送信済みバイト数と不一致で送るとピアが `WT_RESET_STREAM reliable size mismatch` でセッションを閉じる方向であり、`reset_stream(sid, 2**63, 0)` を送るとワイヤは `c0 00 00 00 00 00 00 00` (デコード値 0) となり別ストリームを誤ってリセットする方向である。実行記録は未取得のため、実装時に再測定する
- 既存の `handle_wt_reset_stream` の受信側は `error_code > 0xffffffff` を WT_ERROR、`reliable_size != stream_info.bytes_received` を WT_STREAM_STATE_ERROR で拒否する MUST 実装は入っている (送信側と非対称)

## 設計方針

- `H2Session::reset_stream` の `reliable_size` 引数を廃止し、常に `stream_it->second.bytes_sent` を使う (送信者は自カウンタで正値を知り得るため引数は冗長。CODEBASE.md の破壊的変更許容に従う。非同期ラッパーは `reliable_size` を公開していないため影響なし)
- `H2Session::reset_stream` / `stop_sending` の冒頭で `stream_id` の varint 範囲 (0 〜 2^62 - 1) を検査し、超過は `std::invalid_argument` を投げる。この検査は未知ストリーム検査より先に行う (`2^62` 以上は必ず未知であり、未知ガードを先に置くと黙殺に落ちて `ValueError` が発生しない)
- `H2Session::encode_varint` にも 2^62 - 1 上限の検査を追加し、超過は `std::invalid_argument` を投げる (nanobind の既定翻訳で `ValueError` になる)。`reliable_size` は引数廃止により内部の `bytes_sent` のみになるため防御的検査となる (`error_code` は `uint32_t` のため対象外)
- `H2Session::reset_stream` / `stop_sending` に未知 stream_id の検査を追加し、存在しないストリームには黙って送出しない (現行の終了済みガードと同流儀。セッションは閉じない)。範囲内の未知 ID のみが該当する
- `skills/webtransport-py/SKILL.md` の `reset_stream` の説明 (`def reset_stream(session_id: int, stream_id: int, error_code: int, reliable_size: int = 0) -> None` 箇所) を引数廃止に合わせて更新する
- コード変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h`。これに加えて `skills/webtransport-py/SKILL.md`、テスト、`CHANGES.md` の FIX エントリを更新する
- open issue 0176 の自動応答経路との整合は実装時に確認する。0178 の SETTINGS 値上限検査とは独立 (相互前提を持たない) に実装する
- テストは `tests/test_webtransport_h2_reset_validation.py` を新規作成する

## 完了条件

- `reset_stream(sid, st, code)` が `reliable_size` 引数なしで呼べ、送信済みバイト数がワイヤに載ること
- `stream_id` に 2^62 以上を渡すと `ValueError` が発生すること (`reset_stream` / `stop_sending` の両方)
- 範囲内の未知 stream_id への `reset_stream` / `stop_sending` が送出されず (黙殺)、セッションが閉じないこと
- `tests/test_webtransport_h2_reset_validation.py` を新規作成し、上記 3 経路の回帰テストを追加すること
- `skills/webtransport-py/SKILL.md` の `reset_stream` 説明が引数廃止に更新されていること
- `CHANGES.md` の develop に FIX エントリが追加されていること
- 既存のテスト全 976 件が引き続き通過すること

## 解決方法

- `H2Session::reset_stream` の `reliable_size` 引数を廃止し、常に送信済みバイト数 (`stream_it->second.bytes_sent`) を Reliable Size に載せるようにした (draft-15 Section 6.2)
- `H2Session::reset_stream` / `H2Session::stop_sending` の冒頭で `stream_id` の varint 範囲 (2^62 - 1、RFC 9000 Section 16) を検査し、超過は `std::invalid_argument` (nanobind の既定翻訳で `ValueError`) にした。検査は未知ストリーム検査より先に行う
- `H2Session::encode_varint` にも 2^62 - 1 上限の防御的検査を追加し、`kMaxVarint` を匿名 namespace の共通定数に集約した (ローカル定義 2 箇所も統合)
- `H2Session::reset_stream` / `H2Session::stop_sending` に未知ストリーム ID の検査を追加し、存在しないストリームには黙って送出しないようにした (セッションは閉じない)
- `H2Session::initialize` で Config の上限値を生成時に検査するようにした。`wt_initial_max_data` は 2^62 以上、`wt_initial_max_streams_bidi` / `wt_initial_max_streams_uni` は 2^60 超 (draft-15 Section 6.7 / 6.10) で `ValueError` になる。2xx 応答受信時の nghttp2 コールバック内で `encode_varint` の例外が C ABI 境界を越える経路を塞ぐ
- 高レベル `h2.Client.connect` は Config 上限超えの `ValueError` 時に接続 (writer / reader) を閉じて再送出し、`h2.Server.start` は使い捨てのセッション生成で起動時に Config を検証する
- `tests/test_webtransport_h2_reset_validation.py` を新規作成し、Reliable Size のワイヤ値 (通常・複数チャンク・保留あり)、varint 境界の `ValueError`、終了済みセッションでの検査順序、未知ストリームの送出抑止、Config 上限の境界を検証した
- `tests/prop_webtransport_h2.py` に Config 上限の property test、`tests/test_e2e_webtransport_h2.py` に高レベル `Server.start` / `Client.connect` の Config エラー e2e を追加した
- `skills/webtransport-py/SKILL.md` から `reliable_size` を削除し、`ValueError` / 未知ストリーム抑止 / Config 上限の記載を追加した
- `CHANGES.md` の develop に [CHANGE] (`reliable_size` 廃止) と [FIX] (入力検証・Config 上限) を追加した
- 全 1013 テストが通過することを確認した
