# sdist の方針を決めて CI で検証する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-decide-sdist-policy
- Polished: {YYYY-MM-DD}

## 目的

sdist がビルド・検証・公開のどの経路にも乗っておらず、内容も制御されていない。配布するのかしないのかを決め、決めた方針どおりに CI と設定を揃える。

## 現状

- `pyproject.toml` の `[tool.scikit-build]` に `sdist.include = ["VERSION", "deps.json"]` があるが、`sdist.exclude` は無い
- `VERSION` と `deps.json` は追跡ファイルであり、既定の sdist 収録対象に含まれるため `sdist.include` は実質 no-op である
- 生成される sdist は追跡ファイル 413 件と完全に一致し、`issues/` (209 件) と `refs/` (27 件) も同梱される
- ビルド呼び出しは次の 4 箇所すべてが `--wheel` で、sdist はビルドされない
  - `.github/workflows/wheel.yml` のビルド手順
  - `.github/workflows/wheel.yml` の macOS ビルド手順
  - `.github/workflows/test.yml` の `build_no_ssize_t` ジョブ
  - `.github/workflows/e2e-test.yml` のビルド手順
  - `Makefile` の `wheel` ターゲット
- PyPI へ公開するのは wheel のみである (`publish_wheel` ジョブが wheelhouse の wheel を送る)
- 配布物は CMake ExternalProject で ngtcp2 / nghttp3 / nghttp2 / AWS-LC を GitHub から取得してビルドするため、sdist からのビルドはネットワークとビルドツール (nasm / go 等) を要求する

## 設計方針

- 方針は 2 つに 1 つを選ぶ
  - (a) sdist を配布しない: `sdist.include` を削除し、wheel のみを配布する方針を `README.md` と `pyproject.toml` のコメントに明記する。CI は現状のままでよい
  - (b) sdist も配布する: `sdist.exclude` で `issues/` と `refs/` と `tests/` を除外し、CI に sdist のビルドと検証 (sdist からの wheel ビルドが通ること) を追加する。`.github/workflows/wheel.yml` の公開対象に sdist を加える
- どちらを選ぶかは、ソースからのビルドを利用者に提供するかで決める。現状の CI 構成と README の記載からは wheel のみを配布する意図が読み取れるため、(a) を第一候補とする
- 手元の `dist/` に残っている sdist はローカル生成物であり、方針とは独立に削除してよい (追跡対象外)

## 完了条件

- 方針が決まり、`pyproject.toml` と `README.md` の記載が方針と一致する
- 方針が (b) の場合、CI で sdist のビルドと検証が実行され、公開対象に含まれる
- 方針が (a) の場合、`sdist.include` が削除され、wheel のみを配布する旨が明記される

## 解決方法

- `pyproject.toml` の `[tool.scikit-build]` の `sdist.include` を削除するか、`sdist.exclude` を追加する
- `README.md` の「リリースビルド」の節に配布物の方針を書く
- 方針が (b) の場合は `.github/workflows/wheel.yml` のビルドと公開の手順を更新する
