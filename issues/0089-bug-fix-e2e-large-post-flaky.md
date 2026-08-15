# e2e テスト test_large_post_body の flaky を修正する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-e2e-large-post-flaky
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_e2e_http3.py` の `test_large_post_body` が環境依存で flaky に失敗する問題を調査し、修正する。テスト全体の実行で確実に通る状態にし、CI の信頼性を高める。

## 現状

- 全体テスト実行時に 1 回失敗した (32KB の POST ボディをエコーするテストで、受信データが 12694 バイト目で期待値と不一致)
- 失敗は 1 回のみの観察で、単体実行 9 回連続パス・develop でも 3 回連続パス・全体再実行でもパスしており、再現手順は未確定
- テストは QUIC 経由で 32KB のデータを送受信する実通信 E2E で、データは `bytes((index % 256) for index in range(32 * 1024))` の周期性のあるパターン
- 失敗時のエラー内容 (どの assert で何が不一致だったか) は未記録

## 設計方針

- 原因の切り分けから始める (候補):
  - QUIC / HTTP/3 層のデータ整合性の問題 (パケット喪失・再送・フロー制御・STREAM フレームのオフセット処理)
  - テストのタイミング依存 (イベントの順序・バッファ処理)
  - 高レベル API (client.py / server.py) のデータ分割・結合処理
- 失敗時の詳細ログを取得できる状態にして再現を試みる (繰り返し実行・ロード条件を変えた実行)
- 原因特定後に修正し、回帰テストを追加する
- 変更対象: 原因に応じて `src/bindings/webtransport_h3.cpp` / `src/webtransport/http3/` / `tests/test_e2e_http3.py` のいずれか / `CHANGES.md` (## develop への [FIX] エントリ)

## 完了条件

- `test_large_post_body` が繰り返し実行 (数十回) しても失敗しない
- 原因が特定され、修正内容と根拠が記録されている
- 全テストが通る
