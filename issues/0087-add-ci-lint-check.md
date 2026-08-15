# CI に ruff / ty の実行ステップを追加する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/add-ci-lint-check
- Polished: {YYYY-MM-DD}

## 目的

CI に ruff check / ty check の実行ステップを追加し、lint エラーと型エラーをコミット時ではなく CI で検知できるようにする。「Don't live with broken windows」の原則に反する、lint が壊れたまま CI が通る状態を解消する。

## 現状

- `.github/workflows/` の全ワークフロー (test.yml / e2e-test.yml / wheel.yml) に ruff / ty の実行ステップが存在しない
- prek の ruff-check フックは `entry = "uv run ruff check"` で select 未指定のため、デフォルトルール (E4 / E7 / E9 / F 等) のみを検出する。I001 / RUF022 / PYI034 等のルールはコミット時に検出されず、CI でも検出されない (現に 0083 で修正した 10 件は CI では未検知のままだった)
- `make lint` (`uv run ruff check src/ tests/ examples/`) も select 未指定のため同様にデフォルトルールのみ

## 設計方針

- `shiguredo-github-actions` の規約に従う (GitHub 公式 action を優先、`astral-sh/setup-uv` は利用実績あり)
- CI に `uv run ruff check src/ tests/ examples/` と `uv run ty check src` を実行するステップを追加する
- どのワークフローのどのジョブに追加するか (例: wheel ビルド前に lint して早期失敗させる、test ジョブに追加する) は、既存ワークフローの構成を確認して決める
- ルールの検出範囲は `make lint` と揃えるか、設定を明示的に調整する (現行は select 未指定でデフォルトのみ。I001 / RUF022 / PYI034 を検出対象に含めるかは本 issue で決める)
- 変更対象: `.github/workflows/` 配下のワークフロー / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- CI で `uv run ruff check src/ tests/ examples/` と `uv run ty check src` が実行され、エラーがあればジョブが失敗する
- lint / 型エラーを混入させた状態で CI が通らないことを検証できる (誤って lint を壊した差分で CI が失敗することを確認)
