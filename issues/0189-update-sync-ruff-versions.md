# ローカルと CI の ruff バージョンを同期する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
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
