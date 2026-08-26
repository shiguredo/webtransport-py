# wheel.yml の外部 action をコミットハッシュ固定に統一する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-pin-action-hash
- Polished: 2026-08-26

## 目的

`.github/workflows/wheel.yml` で参照する外部 action のうち、コミットハッシュ固定になっていない 2 件を、リポジトリ内の他 action と同じ「コミットハッシュ固定 + コメント」形式に統一する。浮動ブランチ参照は CI 挙動が非決定的になり、サプライチェーン上も固定方針と不整合である。

## 現状

- `.github/workflows/wheel.yml` の `pypa/gh-action-pypi-publish@release/v1` は浮動ブランチ参照 (`refs/heads/release/v1`。タグではない)
- `.github/workflows/wheel.yml` の `shiguredo/github-actions/.github/actions/slack-notify@main` は浮動ブランチ参照 (`refs/heads/main`)
- 同一リポジトリの他外部 action (actions/checkout、setup-uv 等) は全て `@<40 桁コミットハッシュ> # vX.Y.Z` 形式で固定されている
- `pypa/gh-action-pypi-publish` は GitHub Releases があり、最新リリースタグでピンできる
- `shiguredo/github-actions` には Releases / tags が無く、`slack-notify` に `# vX.Y.Z` 形式のバージョンコメントは付けられない。ブランチ追従のピンではコメントにブランチ名を使う

## 設計方針

- `pypa/gh-action-pypi-publish`: 最新リリースタグのコミットハッシュに固定し、コメントは `# <タグ名>` (例: `# v1.14.2`) とする。`@release/v1` のままハッシュだけ取るのではなく、リリースタグ起点で固定する
- `shiguredo/github-actions/.github/actions/slack-notify`: `main` ブランチ HEAD のコミットハッシュに固定し、コメントは `# main` とする
- 形式はリポジトリ内の他ワークフローと同じ `@<40 桁ハッシュ> # <コメント>` に揃える
- ハッシュとコメントの実値は実装時に GitHub API / `gh` で取得して埋める (issue 本文には固定値を書かない)

## 完了条件

- `wheel.yml` の `pypa/gh-action-pypi-publish` が `@<ハッシュ> # <リリースタグ>` 形式になる
- `wheel.yml` の `slack-notify` が `@<ハッシュ> # main` 形式になる
- `wheel.yml` にタグ名・ブランチ名だけの `@release/v1` / `@main` 参照が残らない
