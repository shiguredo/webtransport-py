# ローカルと CI の ruff バージョンを同期する

- Created: 2026-09-07
- Completed: 2026-09-13
- Branch: feature/update-sync-ruff-versions
- Polished: {YYYY-MM-DD}

## 目的

ローカル実行と CI で異なるバージョンの ruff が使われると、ローカルで検出できない lint 指摘で CI が失敗し往復が発生する。両者のバージョンを同期する仕組みを整える。

## 現状

- prek.toml の ruff-pre-commit は rev v0.16.6 に固定され、prek auto-update で更新する方針である
- 一方ローカルの `uv run ruff` は 0.15.13 であり、pyproject.toml の依存グループに ruff の指定がないためバージョンが固定されない
- 0145 対応の CI で ruff check の RUF059 が失敗したが、ローカルの 0.15.13 では検出されず再現しなかった
- open issue 0090 は検出ルールの select 固定を扱い、ツール自体のバージョン同期は対象外であるため重複しない

## 設計方針

- ローカルと CI で同一バージョンの ruff を使う方針を決める。0090 とは独立に進められるが、前後関係を考慮する
- 変更対象: prek.toml / pyproject.toml の依存グループ / CI ワークフロー / CHANGES.md の misc への [UPDATE] エントリ
- ruff 以外の版差異は対象外とする

## 完了条件

- ローカルと CI で同一バージョンの ruff が使われ、両者の検出結果が一致すること

## 解決方法

- `pyproject.toml` の dev 依存グループに `ruff==0.16.6` を追加し、`prek.toml` の ruff-pre-commit (`rev = "v0.16.6"`) と同じバージョンに固定した。更新時は両方を同時に上げる旨をコメントに残した
- `uv lock` で `uv.lock` を更新し、`uv run ruff --version` が 0.16.6 を返すことを確認した
- ローカルの ruff 0.16.6 で `ruff check src/ tests/ examples/` と `ruff format --check src/ tests/ examples/` が通ることを確認した (CI と同じ検出結果)
- CI は prek-action 経由で ruff-pre-commit を使うため、これでローカルと CI のバージョンが一致する
