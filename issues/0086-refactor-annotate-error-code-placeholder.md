# HTTP/2 のエラーコード 0x50 (FLOW_CONTROL_ERROR) が 0xTBD のプレースホルダである旨をコメントに明記する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-annotate-error-code-placeholder
- Polished: 2026-09-05

## 目的

draft-ietf-webtrans-http2-15 Section 3.4 のエラーコードは 0xTBD (未割り当て) であり (Section 11.3 は IANA 登録で Section 3.4 を参照する)、コード内で使っている 0x50 は正式値ではないプレースホルダ。プレースホルダである旨をコードコメントとテスト docstring に明記し、正式コードであるかのように誤読されるのを防ぐ。挙動は変更しない。

## 現状

- `src/bindings/webtransport_h2.cpp` の定数定義 (`kWtFlowControlError`) は 0xTBD プレースホルダ旨の注記済みである。本 issue では定数定義を対象外とし、使用箇所周辺のコメントのみを扱う
- フロー制御違反の使用経路は helper 経由で複数に拡大しており、起票時の 2 箇所 (`handle_wt_stream` の受信超過 / `send_stream_data` の送信超過) に留まらない。使用箇所周辺に 0xTBD 言及がないものが残件である
- コメントは「FLOW_CONTROL_ERROR」と名前のみで、0xTBD のプレースホルダである旨の明記がないものが残る。draft で値が確定したら更新する、という追跡の手掛かりもないものが残る
- フロー制御違反に言及するテストの多くは注記済みである。残件は `tests/test_webtransport_h2_close_session.py` の二重呼び出しテスト等の未注記分に限定される
- open issue 0084 は WT_STREAM_STATE_ERROR の 0x51 を同じプレースホルダ方式で仮採用するが (「draft で値が確定したら更新する旨をコメントで明記する」)、0x50 のフロー制御違反は 0084 のスコープ外と明記されており、本 issue の対象である

## 設計方針

- 使用箇所周辺のコメントと、未注記のテスト docstring に「0xTBD (未確定) のプレースホルダ。draft-ietf-webtrans-http2-15 Section 3.4 で値が確定したら更新する」旨を明記する (0084 で WT_STREAM_STATE_ERROR の 0x51 に付けたコメントと同じ方式・同じ節番号。定数定義は注記済みのため触らない)
- 挙動は一切変更しない。コードの書き換えはコメントのみ
- 変更対象: `src/bindings/webtransport_h2.cpp` (使用箇所周辺コメント) / 未注記のテスト (docstring) / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- 使用箇所周辺の残件コメントと、未注記テストの docstring にプレースホルダである旨が明記される
- 全テストが通り、挙動が変わらないこと (フロー制御違反テストが従来どおり通る)
