# HTTP/2 のエラーコード 0x50 (FLOW_CONTROL_ERROR) が 0xTBD のプレースホルダである旨をコメントに明記する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-annotate-error-code-placeholder
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 11.3 のエラーコードは 0xTBD (未割り当て) であり、コード内で使っている 0x50 は正式値ではないプレースホルダ。プレースホルダである旨をコードコメントとテスト docstring に明記し、正式コードであるかのように誤読されるのを防ぐ。挙動は変更しない。

## 現状

- `src/bindings/webtransport_h2.cpp` のフロー制御違反で 0x50 を使用している 2 箇所:
  - `handle_wt_stream` の受信超過 (Error イベントの error_code)
  - `send_stream_data` の送信超過 (FLOW_CONTROL_ERROR による `close_session` 呼び出し)
- コメントは「FLOW_CONTROL_ERROR」と名前のみで、0xTBD のプレースホルダである旨の明記がない。draft で値が確定したら更新する、という追跡の手掛かりもない
- フロー制御違反に言及するテスト (close_session の二重呼び出しテスト等) の docstring も「FLOW_CONTROL_ERROR (0x50)」と正式値であるかのように読める
- open issue 0084 は WT_STREAM_STATE_ERROR の 0x51 を同じプレースホルダ方式で仮採用するが (「draft で値が確定したら更新する旨をコメントで明記する」)、0x50 のフロー制御違反は 0084 のスコープ外と明記されており、本 issue の対象である

## 設計方針

- 0x50 を使用している 2 箇所のコメントと、フロー制御違反に言及するテストの docstring に「0xTBD (未確定) のプレースホルダ。draft-ietf-webtrans-http2-15 Section 11.3 で値が確定したら更新する」旨を明記する (0084 で WT_STREAM_STATE_ERROR の 0x51 に付ける予定のコメントと同じ方式)
- 挙動は一切変更しない。コードの書き換えはコメントのみ
- 変更対象: `src/bindings/webtransport_h2.cpp` (コメント) / フロー制御違反に言及するテスト (docstring) / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- 0x50 を使用している全箇所のコメントと、フロー制御違反に言及する全テストの docstring にプレースホルダである旨が明記される
- 挙動が変わらないこと (フロー制御違反テストが従来どおり通る)
- 全テストが通る
