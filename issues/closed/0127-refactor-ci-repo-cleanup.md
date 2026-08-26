# CI のトリガー条件と README の第三者ライセンス節を整備する

- Created: 2026-08-18
- Completed: 2026-08-26
- Branch: feature/refactor-ci-repo-cleanup
- Polished: {YYYY-MM-DD}

## 目的

CI のトリガー条件の問題 (tests/ のみの変更で単体テストが実行されない) と、README と THIRD_PARTY_LICENSES.md のライセンス情報の重複・欠落を整備する。

## 現状

- **tests/ のみの変更で CI が単体テストを実行しない**: `.github/workflows/wheel.yml` の `paths-ignore` に `tests/**` が含まれており、テストファイルだけを変更した push では単体テスト (test.yml のジョブ) が一度も実行されない。e2e-test.yml は push で動くがブラウザテストのみ。コミット時の prek フックで代替される設計だが、CI 単体として tests/ 変更が未検証になる
- **README のライセンス節が THIRD_PARTY_LICENSES.md と重複**: README.md に ngtcp2 / nghttp3 / nghttp2 の MIT ライセンス全文が THIRD_PARTY_LICENSES.md と完全重複して記載されている。一方、依存する唯一の暗号ライブラリ AWS-LC は README に記載がない

## 設計方針

- wheel.yml の `paths-ignore` から `tests/**` を外す (または tests/ 変更でも単体テストが走るトリガーを追加する)。対象範囲を精査して決定する
- README のライセンス節を削除し、THIRD_PARTY_LICENSES.md へのリンクに置き換える (AWS-LC の欠落も同時に解消する)

## 完了条件

- tests/ のみの変更でも単体テストが CI で実行される
- README のライセンス節が THIRD_PARTY_LICENSES.md へのリンクに置き換わる

## 解決方法

- `.github/workflows/wheel.yml` の `paths-ignore` から `tests/**` を削除した。tests/ のみの push でも wheel ビルド → `test.yml` 経由で単体テストが走る
- `README.md` から ngtcp2 / nghttp3 / nghttp2 のライセンス全文節を削除し、`## 第三者ライセンス` 節と `THIRD_PARTY_LICENSES.md` へのリンクを追加した。プロジェクト本体の `## ライセンス` (Apache License 2.0) は残した
- `CHANGES.md` の `### misc` に CI 変更の `[UPDATE]` エントリを追加した (README 変更は changelog 規約により記載しない)
