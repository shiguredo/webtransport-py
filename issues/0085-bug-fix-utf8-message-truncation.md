# HTTP/2 の close_session のエラーメッセージ切り詰めが UTF-8 文字境界を無視する問題を修正する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-utf8-message-truncation
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http2-15 Section 6.12 の MUST「Senders that truncate an application-supplied message MUST do so at a UTF-8 character boundary」違反を修正する。1024 バイトを超えるマルチバイト文字 (日本語等) を含むエラーメッセージが文字境界を無視して切り詰められると、不完全な UTF-8 シーケンスがワイヤへ送出され、受信側は同 Section の MUST により不正な UTF-8 を WT_ERROR セッションエラーとして扱う。

## 現状

- `src/bindings/webtransport_h2.cpp` の `close_session` は `std::min(error_message.size(), 1024)` のバイト単位でエラーメッセージを切り詰める。UTF-8 文字境界を考慮しないため、マルチバイト文字が 1024 バイト境界を跨ぐと不完全な UTF-8 シーケンスが送出される
- 受信側 (`handle_wt_close_session`) は 1024 バイト超過・不正 UTF-8 の WT_CLOSE_SESSION を検知して WT_ERROR にしている (対応済み)。本 issue のスコープは送信側の切り詰めのみ
- テストは 1024 バイト超のマルチバイトメッセージを未カバー (既存テストは短い ASCII メッセージのみ)

## 設計方針

- `close_session` の切り詰めを UTF-8 文字境界で行う: バイト単位で 1024 に切り詰めた後、末尾が不完全な UTF-8 シーケンスなら直前の文字境界まで後退させる (マルチバイト文字の先頭バイトを探して境界を調整する)
- テスト: 1024 バイトを跨ぐ日本語メッセージを `close_session` で送信し、ワイヤ上の Application Error Message が有効な UTF-8 であり 1024 バイト以下であることを検証する (既存の Sans-IO 構成とワイヤ部分列チェックを使う)。日本語メッセージが境界で切れないことも検証する
- 変更対象: `src/bindings/webtransport_h2.cpp` (切り詰めロジック・コメント) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- マルチバイト文字が 1024 バイト境界を跨ぐエラーメッセージでも、ワイヤへ送出される Application Error Message が常に有効な UTF-8 で 1024 バイト以下になる
- ASCII のみのメッセージの切り詰めは従来どおり 1024 バイトで行われる
- 全テストが通る
