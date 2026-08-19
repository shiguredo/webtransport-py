# テストの欠落と PBT の網羅を強化する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-strengthen-tests
- Polished: {YYYY-MM-DD}

## 目的

レビューで見つかったテストの欠落領域と、PBT (Property-Based Testing) の網羅性の低さを改善する。エラーパス・境界値・プロトコル不変条件の検証を追加し、回帰を早期に検出できるようにする。

## 現状

- **エラーパスのテスト欠落**: プロトコルエラー時の高レベル層の挙動 (run() のハング等) を検出するテストがない。任意バイト列を渡す PBT は「クラッシュしない」ことしか検証しない
- **フロー制御カプセルのテスト欠落**: WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS の減少値検出 (issue 0097 と対応) を検証するテストがない
- **境界値テストの欠落**: WT_CLOSE_SESSION の 1024 バイト超・不正 UTF-8、error_code の 32bit 範囲超過、過大データグラム等
- **PBT の偏り**: `tests/prop_webtransport_h3.py` / `prop_webtransport_h2.py` / `prop_isolation_h2.py` / `prop_isolation_h3.py` の大半は「任意の値を設定・渡してもクラッシュしない」検証で、プロトコル不変条件 (セッション終了後の送信無視・フロー制御超過のエラー送出等) を property 化していない
- **flaky 要因**: `tests/test_e2e_webtransport_h2.py` に固定 0.1 秒 sleep のマジック待ちがある。`tests/test_e2e_webtransport_h3.py` に 60 秒の wait_for があり CI の `--timeout=30` と不整合 (時間切れが意図しない失敗になる)

## 設計方針

- エラーパス・境界値のテストを追加する (各修正 issue の完了条件にあるテスト追加と連携する)
- PBT で検証できるプロトコル不変条件を property 化する (確立 → 送信 → 終了学習 → 送信無視、フロー制御超過等)
- 固定 sleep をイベント待ちに置き換え、wait_for のタイムアウトを CI の制限と整合させる

## 完了条件

- 上記のテストが追加され、全テストが通る
- flaky 要因が解消される
