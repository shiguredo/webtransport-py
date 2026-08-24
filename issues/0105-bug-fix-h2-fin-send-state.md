# WebTransport over HTTP/2 の FIN 送出後の送信側状態遷移を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-fin-send-state
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http2-15 Section 6.4 の MUST「A WT_STREAM capsule MUST NOT be sent after a stream is closed or reset」に反し、FIN 送出後に再度 `send_stream_data` を呼ぶと閉じたストリームへ WT_STREAM が送出される問題を修正する。併せて Section 6.2 の MUST「A WT_RESET_STREAM capsule MUST NOT be sent after a stream is closed or reset」に反する、FIN 送出後の `reset_stream` とリセット送出後の再リセットも対象とする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::send_stream_data` は `send_state` が ResetSent の場合のみ送信を塞ぎ、WT_STREAM_FIN 送出後に送信側状態を DataSent へ遷移させない (コメントでスコープ外と明記)
- FIN 後に再度 `send_stream_data` を呼ぶと、閉じたストリームへの WT_STREAM がワイヤに送出され、ピアから WT_STREAM_STATE_ERROR (0x51) を受ける
- `H2Session::reset_stream` は send_state を一切確認せず、FIN 送出後 (DataSent 相当) やリセット送出後 (ResetSent) に再度呼ぶと WT_RESET_STREAM がワイヤに送出される (ピアはクリーンクローズ済みストリームへの WT_RESET_STREAM を受信してセッションエラーとする)
- 0084 (closed) により受信側の WT_STREAM_STATE_ERROR 検知は実装済みであり、本 issue は送信側のガードを担当する

## 設計方針

- FIN (WT_STREAM_FIN) 送出時 (send_stream_data の fin=True。空の起動 WT_STREAM_FIN を含む) に送信側状態を DataSent へ遷移させ、以後の `send_stream_data` を塞ぐ
- DataSent 状態の `reset_stream` も無視する (closed 後の WT_RESET_STREAM は Section 6.2 の MUST NOT)
- ResetSent 状態の `reset_stream` (再リセット) も無視する (既存の send_stream_data の ResetSent ガードと対称にする)
- 変更対象: `src/bindings/webtransport_h2.cpp` (send_stream_data / reset_stream の送信側状態遷移) / テスト / CHANGES.md (## develop セクションへの [FIX] エントリ)

## 完了条件

- FIN 送出後の `send_stream_data` が無視される
- FIN 送出後の `reset_stream` が無視される
- リセット送出後の再 `reset_stream` が無視される
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加され、全テストが通る
