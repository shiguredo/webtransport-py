# WebTransport over HTTP/2 の送信残クレジット観測 API を追加する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-send-credit-api
- Polished: {YYYY-MM-DD}

## 目的

フロー制御の動作確認や E2E テストでは、送信側の残クレジットをアプリが観測できると検証が容易になる。現状はワイヤの `WT_MAX_*` カプセル列でしか確認できない。セッションレベルの残量を返す観測専用 API を追加する。issue 0156 (クレジット補充) から分離した機能追加であり、0156 の完了には含めない。

## 現状

- `H2Session` に残クレジットを返す公開 API は無い
- 送信側の上限管理は `max_data_local` と `bytes_sent` の差分で内部的に行われている

## 設計方針

- `H2Session::get_send_credit(session_id)` を追加し、セッションレベルの送信可能残量 (`max_data_local - bytes_sent`、負値は 0) を返す。存在しないセッション ID では 0 を返す
- Python バインディングに同名で公開する
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとし、テストと `CHANGES.md` の ADD エントリを付ける

## 完了条件

- `get_send_credit` が残量を返すこと (枯渇時は 0 になること)
- `tests/` に観測 API のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
