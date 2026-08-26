# 配布 wheel に THIRD_PARTY_LICENSES.md を同梱する

- Created: 2026-08-18
- Completed: 2026-08-26
- Branch: feature/add-license-in-wheel
- Polished: 2026-08-26

## 目的

ビルドした wheel に `THIRD_PARTY_LICENSES.md` が含まれておらず、静的リンクする依存ライブラリ (ngtcp2 / nghttp3 / nghttp2 は MIT、AWS-LC は Apache-2.0 OR ISC 等) の著作権表示・ライセンス通知義務を満たしていない問題を修正する。配布 wheel に `THIRD_PARTY_LICENSES.md` を同梱する。

## 現状

- `pyproject.toml` の `license-files = ["LICENSE"]` により、wheel の `*.dist-info/licenses/LICENSE` にはプロジェクト本体の LICENSE が既に同梱されている (METADATA の `License-File: LICENSE` としても記録される)
- 一方 `THIRD_PARTY_LICENSES.md` は `license-files` に含まれていないため、wheel には同梱されない (最新の `dist/*.whl` で確認済み)
- `THIRD_PARTY_LICENSES.md` はリポジトリ直下に存在し、sdist には通常ファイルとして含まれるが、PyPI 公開は `wheel.yml` 経由の wheel のみであるため、wheel への同梱が必要
- `tool.scikit-build.sdist.include` と `tool.scikit-build.wheel.packages` はライセンス同梱経路ではない。scikit-build-core は `project.license-files` を `dist-info/licenses/` へコピーする

## 設計方針

- `pyproject.toml` の `project.license-files` に `THIRD_PARTY_LICENSES.md` を追加する (既存の `LICENSE` エントリは残す)
- `tool.scikit-build.wheel.license-files` は使わない。scikit-build-core は `project.license-files` との併用をエラーにするため、正攻法は `project.license-files` のみとする
- ビルド後の wheel を展開し、`*.dist-info/licenses/THIRD_PARTY_LICENSES.md` の同梱を確認する

## 完了条件

- `project.license-files` に `LICENSE` と `THIRD_PARTY_LICENSES.md` の両方が含まれる
- ビルドした wheel の `*.dist-info/licenses/` に `LICENSE` と `THIRD_PARTY_LICENSES.md` の両方が含まれる

## 解決方法

- `pyproject.toml` の `project.license-files` に `THIRD_PARTY_LICENSES.md` を追加した
- `uv build --wheel` で生成した wheel を展開し、`*.dist-info/licenses/THIRD_PARTY_LICENSES.md` と METADATA の `License-File: THIRD_PARTY_LICENSES.md` を確認した
