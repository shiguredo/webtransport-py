# ruff の検出ルールを select で明示的に固定する

- Created: 2026-08-15
- Completed: 2026-09-13
- Branch: feature/refactor-explicit-ruff-select
- Polished: {YYYY-MM-DD}

## 目的

ruff の検出ルールを select で明示的に固定し、ruff のバージョン更新によるデフォルトルールの変化で lint の検出挙動が変わるのを防ぐ。0087 で CI に ruff check を追加する際「select の明示は行わずローカルと CI で同じデフォルトルールを使う」方針としたが、デフォルトルールは ruff のバージョンに依存する (ruff 0.16 で 59 → 413 ルールに拡大された実績がある) ため、明示的な固定の検討を本 issue で行う。

## 現状

- `pyproject.toml` の `[tool.ruff]` は target-version / line-length のみで select 未指定
- prek の ruff-check フック・`make lint`・CI (0087 で追加予定) はすべて select 未指定で、ruff のデフォルトルールに依存する
- ruff 0.16 でデフォルトルールが 59 → 413 件に拡大された実績があり、バージョン更新で検出挙動が変わり得る

## 設計方針

- `[tool.ruff]` に select (と必要なら ignore) を明示的に設定し、検出ルールを固定する
- 0087 の完了後に実装する (本 issue を 0087 より先に対応すると、0087 が追加する CI の検出挙動が本 issue の select 設定に依存し、0087 単体での検証ができなくなるため)
- 明示するルールセットは、現行の検出挙動 (デフォルト 413 ルール) を維持する最小セットを基準に決める (全デフォルトルールの列挙か、挙動維持を満たす主要ルールの列挙か)
- 変更対象: `pyproject.toml` ([tool.ruff]) / 影響範囲の確認 (`prek.toml` / `Makefile` / CI) / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- `[tool.ruff]` に select が明示され、検出ルールがバージョンに依存しない
- `uv run ruff check src/ tests/ examples/` の検出結果が変更前後で変わらない (挙動の維持)
- 全テストが通る

## 解決方法

- `pyproject.toml` に `[tool.ruff.lint]` を追加し、`select` に ruff 0.16.6 の既定ルール 413 件をコード単位で列挙した。カテゴリ単位 (`"B"` など) で書くと既定に含まれないルールまで有効になり検出結果が変わるため、コード単位で列挙している
- 変更前後で有効ルールが完全に一致することを確認した (`ruff check --show-settings` の `linter.rules.enabled` を diff して差分なし)
- `ruff check src/ tests/ examples/` の検出結果が変わらないこと (指摘 0 件) を確認した
- バージョン更新時は `uv run ruff check --show-settings <file>` の `linter.rules.enabled` を見て差分を反映する旨をコメントに残した
