# CI に ruff / ty の実行ステップを追加する

- Created: 2026-08-15
- Completed: 2026-08-15
- Branch: feature/add-ci-lint-check
- Polished: 2026-08-15

## 目的

CI に ruff check / ty check の実行ステップを追加し、lint エラーと型エラーを CI で強制的に検知できるようにする。現状はコミット時の prek フックのみが検知手段で、CI には強制がない (「Don't live with broken windows」の原則に反する)。

## 現状

- `.github/workflows/` の全ワークフロー (test.yml / e2e-test.yml / wheel.yml) に ruff / ty の実行ステップが存在しない
- コミット時には prek の ruff-check フック (`entry = "uv run ruff check"`) と ty フックが検知する。ruff-check は select 未指定だが、ruff 0.16 以降はデフォルトルールが大幅に拡大され (I001 / RUF022 / PYI034 を含む 413 ルール)、0083 で修正した 10 件 (I001 / RUF022 / PYI034) もデフォルトルールで検出された
- つまり lint / 型チェックの検知手段は存在するが、CI での強制がない (lint / 型エラーを混入した差分でも CI は通る)

## 設計方針

- 既存の test.yml のテストジョブに lint ステップを追加する (テストと一緒に実行)。専用ワークフロー・独立した lint ジョブは新設しない (専用の仕組みはメンテナンスコストが高くなるため)
- 追加位置: 各テストジョブの Run tests ステップの前に Run lint ステップを追加する
- lint の実行は pyproject.toml の設定に従う: ruff / ty は dependency-groups の lint グループ (`lint = ["ruff", "ty"]`) にあり、テストグループ (`test`) とは分離済み。lint ステップでは lint グループをインストールして実行する (例: `uv sync --only-group test` に `--group lint` を追加、または lint ステップで `uv run --group lint` を使う)
- 実行コマンドは `uv run ruff check src/ tests/ examples/` と `uv run ty check src` (ruff は make lint と、ty は prek と同一)。select の明示は行わず、ローカルと CI で同じデフォルトルールを使う (select の明示的な固定は open issue 0090 のスコープ)。リポジトリルートの dev.py は prek の対象に含まれるが make lint / CI のコマンドの対象外という既存の差異は維持する
- 既知の制約:
  - test.yml は workflow_call / workflow_dispatch 専用のため、push 時は wheel.yml 経由で実行される
  - wheel.yml の paths-ignore (tests/**) のため、tests/ のみの変更では lint も実行されない (lint の主対象は src/ のため許容)
- setup-uv は python-version 3.14 を明示する (既存ワークフローと同じ。未指定だと uv が最新 managed Python を選択し、ローカルと CI で挙動が非決定的になる)
- action は GitHub 公式 (`actions/checkout`) と許可済みの `astral-sh/setup-uv` のみを使う (shiguredo-github-actions の規約)
- 変更対象: `.github/workflows/test.yml` / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- テストジョブで `uv run ruff check src/ tests/ examples/` と `uv run ty check src` が実行され、いずれかがエラーを返すとジョブが失敗する (tests/ のみの変更など、wheel.yml が実行されないケースでは lint も実行されない既知の制約)
- lint エラーを混入した一時コミットで CI が失敗することを確認し、確認後に revert して CI が通ることを確認する (型チェックの検知限界 (公開型の解決) は open issue 0077 のスコープ)
- ローカルの `uv run pytest tests/ -v --timeout=30` が通る

## 解決方法

- `.github/workflows/test.yml` のテストジョブ (test_ubuntu / test_macos) に Run lint ステップを追加した。専用ワークフローは新設せず、テストと一緒に lint を実行する形とした (専用の仕組みはメンテナンスコストが高くなるため)
- lint の実行は pyproject.toml の設定に従う: `uv sync --only-group test` を `uv sync --only-group test --group lint` に変更して ruff / ty (dependency-groups の lint グループ) をインストールし、`uv run ruff check src/ tests/ examples/` と `uv run ty check src` を実行する
- `ty check src` はチェックアウト (拡張モジュール・.pyi 無し) でもエラーなしで通ることを確認した (拡張モジュールの型は Unknown として扱われる)
- `CHANGES.md` の `### misc` に [UPDATE] エントリを追加した
- lint エラーを混入した一時ブランチで CI が失敗することを確認し、一時ブランチを削除した (一時コミットの revert は不要な構成で検証)
- 全テスト (664 本) が通ることを確認した
