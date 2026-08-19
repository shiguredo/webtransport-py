# WebTransport over HTTP/2 の FIN 送出後の送信側状態遷移を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-fin-send-state
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.4 の MUST「ストリームが閉じられた後やリセット後に WT_STREAM を送ってはならない」に反し、FIN 送出後に再度 `send_stream_data` を呼ぶと閉じたストリームへ WT_STREAM が送出される問題を修正する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::send_stream_data` は `send_state` が ResetSent の場合のみ送信を塞ぎ、WT_STREAM_FIN 送出後に送信側状態を DataSent へ遷移させない (コメントでスコープ外と明記)
- FIN 後に再度 `send_stream_data` を呼ぶと、閉じたストリームへの WT_STREAM がワイヤに送出され、ピアから WT_STREAM_STATE_ERROR を受ける

## 設計方針

- FIN (WT_STREAM_FIN) 送出後に送信側状態を DataSent へ遷移させ、以後の `send_stream_data` を塞ぐ
- リセット後の再リセット呼び出しの扱いも整理する
- テストを追加する

## 完了条件

- FIN 送出後の `send_stream_data` が無視される
- テストが追加される
