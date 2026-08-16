# CI に ruff / ty の実行ステップを追加する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/add-ci-lint-check
- Polished: 2026-08-15

## 目的

CI に ruff check / ty check の実行ステップを追加し、lint エラーと型エラーを CI で強制的に検知できるようにする。現状はコミット時の prek フックのみが検知手段で、CI には強制がない (「Don't live with broken windows」の原則に反する)。

## 現状

- `.github/workflows/` の全ワークフロー (test.yml / e2e-test.yml / wheel.yml) に ruff / ty の実行ステップが存在しない
- コミット時には prek の ruff-check フック (`entry = "uv run ruff check"`) と ty フックが検知する。ruff-check は select 未指定だが、ruff 0.16 以降はデフォルトルールが大幅に拡大され (I001 / RUF022 / PYI034 を含む 413 ルール)、0083 で修正した 10 件 (I001 / RUF022 / PYI034) もデフォルトルールで検出された
- つまり lint / 型チェックの検知手段は存在するが、CI での強制がない (lint / 型エラーを混入した差分でも CI は通る)

## 設計方針

- 専用ワークフロー `lint.yml` を新設し、push 無条件で実行する (ubuntu-slim runner)。既存ワークフローへの追加は以下の理由で不適切:
  - test.yml は workflow_call / workflow_dispatch 専用で、push では単独実行されない
  - wheel.yml は paths-ignore で tests/** を無視するため、tests/ のみの変更では実行されない (lint は tests/ も対象)
  - e2e-test.yml は macOS のみで、lint 実行に macOS runner は非効率
- 実行コマンドは `uv run ruff check src/ tests/ examples/` と `uv run ty check src` (ruff は make lint と、ty は prek と同一)。select の明示は行わず、ローカルと CI で同じデフォルトルールを使う (select の明示的な固定は open issue 0090 のスコープ)。リポジトリルートの dev.py は prek の対象に含まれるが make lint / CI のコマンドの対象外という既存の差異は維持する
- setup-uv は python-version 3.14 を明示する (既存ワークフローと同じ。未指定だと uv が最新 managed Python を選択し、ローカルと CI で挙動が非決定的になる)
- action は GitHub 公式 (`actions/checkout`) と許可済みの `astral-sh/setup-uv` のみを使う (shiguredo-github-actions の規約)
- 変更対象: `.github/workflows/lint.yml` (新規) / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- push 時に lint ジョブが実行され、`uv run ruff check src/ tests/ examples/` と `uv run ty check src` のいずれかがエラーを返すとジョブが失敗する
- lint エラーを混入した一時コミットで CI が失敗することを確認し、確認後に revert して CI が通ることを確認する (型チェックの検知限界 (公開型の解決) は open issue 0077 のスコープ)
- ローカルの `uv run pytest tests/ -v --timeout=30` が通る
