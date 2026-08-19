# wheel.yml の外部 action をコミットハッシュ固定に統一する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-pin-action-hash
- Polished: {YYYY-MM-DD}

## 目的

`.github/workflows/wheel.yml` で参照する外部 action のうち、コミットハッシュ固定になっていない 2 件を、リポジトリ内の他 action と同じ「コミットハッシュ固定 + バージョンコメント」形式に統一する。浮動タグ・浮動ブランチ参照は CI 挙動が非決定的になり、サプライチェーン上も固定方針と不整合である。

## 現状

- `.github/workflows/wheel.yml` の `pypa/gh-action-pypi-publish@release/v1` は浮動タグ参照
- `.github/workflows/wheel.yml` の `shiguredo/github-actions/.github/actions/slack-notify@main` は浮動ブランチ参照
- 同一リポジトリの他 action (actions/checkout、setup-uv 等) は全て `@コミットハッシュ # vX.Y.Z` 形式で固定されている

## 設計方針

- 両 action をコミットハッシュ固定 + バージョンコメントの形式に更新する (各 action の最新リリースを確認して固定する)
- リポジトリ内の他ワークフローと同形式に統一する

## 完了条件

- wheel.yml の全外部 action がコミットハッシュ固定 + バージョンコメント形式になる
