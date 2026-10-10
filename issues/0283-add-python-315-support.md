# Python 3.15 / 3.15t を 3.14 と併存でサポートする

- Created: 2026-10-10
- Completed: {YYYY-MM-DD}
- Branch: feature/add-python-315-support

## 目的

uv が Python 3.15 を導入できるようになったため、対応バージョンに 3.15 / 3.15t を追加する。3.14 / 3.14t のサポートは維持し、4 バージョン分の wheel をビルド・テスト・公開できる状態にする。

MOQT を扱う利用側 (moqt-py) が新しい Python へ追従する際、webtransport-py が 3.15 の wheel を出していないと依存の更新が止まる。

## 現状

- `pyproject.toml` の `requires-python` は `>=3.14` だが、classifier は `Programming Language :: Python :: 3.14` のみで、対応バージョンの記載は README と `skills/webtransport-py/SKILL.md` にも 3.14 / 3.14t とだけある
- CI のマトリクス (`.github/workflows/wheel.yml` / `test.yml` / `e2e-test.yml`) は 3.14 / 3.14t のみを対象にしており、3.15 の wheel はビルドもテストもされない
- 3.15 でのビルドは未検証だった。`CMakeLists.txt` の `nanobind_add_module` は `FREE_THREADED` を指定しており、nanobind 3.1.0 と scikit-build-core 1.1.1 が 3.15 の C API と free-threading の宣言 (`Py_mod_gil_not_used`) に対応するかが不明だった
- ローカルの `.python-version` は 3.14 で、開発用 venv と `make develop` / `make test` は 3.14 を使う

## 設計方針

- ビルド構成の変更は行わない。3.15 / 3.15t は既存の scikit-build-core + nanobind の構成でそのままビルドできる (確認済み)
- CI のマトリクスへ 3.15 / 3.15t を追加する。対象は wheel のビルド (`build_ubuntu` / `build_macos`)、wheel を使ったテスト (`test_ubuntu` / `test_macos`)、PyPI 公開 (`publish_wheel`) の 4 バージョン分とする
- ブラウザ E2E (`test_browser`) は GIL ありの 3.14 / 3.15 のみを対象にする。従来 3.14t も対象外であり、拡張モジュールの Python バージョン依存が小さいため
- `build_no_ssize_t` は非推奨 nghttp2 API のコンパイル回帰検出であり Python バージョンに依存しないため、3.14 のみを維持する
- classifier に `Programming Language :: Python :: 3.15` を追加する。併存のため `requires-python` は `>=3.14` のままとする
- README と `skills/webtransport-py/SKILL.md` の対応バージョン表記を 3.14 / 3.14t / 3.15 / 3.15t に更新する
- ローカルの既定は 3.14 を維持する。3.15 のローカルビルドは `uv build --wheel --python 3.15` のように interpreter を指定して行う

## 完了条件

- Python 3.15 / 3.15t で wheel がビルドできる
- 3.15t でビルドした拡張モジュールを import しても GIL が有効化されない (nanobind の free-threading 宣言が働く)
- 3.15 / 3.15t で全テストが通過する
- CI のマトリクスが wheel のビルド・テスト・公開の 4 バージョン分と、ブラウザ E2E の 2 バージョン分を含む
- prek のフック (ruff / ty / pytest など) がすべて通過する
