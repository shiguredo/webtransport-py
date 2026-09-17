# ubuntu-26.04 と ubuntu-26.04-arm をテスト対象プラットフォームに追加する

- Created: 2026-09-17
- Completed: 2026-09-17
- Branch: feature/add-ubuntu-2604-runners
- Polished: {YYYY-MM-DD}

## 目的

Ubuntu 26.04 LTS (x86_64 / arm64) を対応プラットフォームに加える。公開する wheel は 24.04 でビルドした manylinux_2_39 のままで 26.04 でも動作するため、26.04 ではその wheel を導入してテストを実行し、動作を継続的に検証できるようにする。

## 現状

- `.github/workflows/wheel.yml` の `build_ubuntu` は `ubuntu-24.04` / `ubuntu-24.04-arm` で wheel をビルドし、`publish_wheel` も同じ 2 プラットフォームを PyPI へ公開する
- `.github/workflows/test.yml` の `test_ubuntu` は同じ 2 プラットフォームで wheel を導入してテストする
- `README.md` の「プラットフォーム」は Ubuntu 24.04 LTS x86_64 / arm64 と macOS 26 arm64 のみを挙げている
- 26.04 をビルド対象に加えると、生成される wheel が 24.04 のものと同じファイル名になる。実際に CI を実行して両方の artifact を取得し、`webtransport_py-<version>-cp314-cp314-manylinux_2_39_x86_64.whl` のように同名であることを確認した
- 26.04 x86_64 の free-threaded (3.14t) ビルドは auditwheel で repair できない。nanobind が Linux x86_64 で `-mtls-dialect=gnu2` を有効にするため (`nanobind-config.cmake` の `NB_HAS_MTLS_GNU2`)、glibc 2.41 上でビルドした拡張モジュールが `GLIBC_ABI_GNU2_TLS` を要求する。auditwheel の `manylinux-policy.json` には `GLIBC_ABI` 名前空間が無く、manylinux_2_39 でも manylinux_2_41 でも repair できない

## 設計方針

- 26.04 はテスト専用のプラットフォームとし、`.github/workflows/test.yml` の `test_ubuntu` に追加する。導入する wheel は 24.04 でビルドした artifact を使う
- `.github/workflows/wheel.yml` の `build_ubuntu` と `publish_wheel` は 24.04 と macOS のままにする。26.04 でビルドしても 24.04 と同じ manylinux_2_39 になり、公開物として区別できないため
- プラットフォームの対応は matrix の項目で表現する。テストで導入する artifact 名を `artifact` として各項目に持たせ、26.04 の項目は 24.04 の artifact を指す
- `README.md` の「プラットフォーム」には Ubuntu 26.04 LTS x86_64 / arm64 を追加する。公開する manylinux_2_39 の wheel は 26.04 でも動作し、CI で 26.04 のテストを実行するため
- ブラウザ E2E (`.github/workflows/e2e-test.yml`) は macOS のみで実行する検証であり、本 issue では変更しない

## 完了条件

- `.github/workflows/test.yml` の `test_ubuntu` が `ubuntu-26.04` / `ubuntu-26.04-arm` を含み、24.04 でビルドした wheel を導入してテストする
- `.github/workflows/wheel.yml` の `build_ubuntu` と `publish_wheel` は 24.04 と macOS のみである
- `README.md` の「プラットフォーム」に Ubuntu 26.04 LTS x86_64 / arm64 が含まれる
- GitHub Actions で 26.04 のテストジョブが成功する

## 解決方法

- `.github/workflows/test.yml` の `test_ubuntu` の `platform` に `ubuntu-26.04_x86_64` / `ubuntu-26.04_arm64` を追加し、`artifact` で `ubuntu-24.04_x86_64` / `ubuntu-24.04_arm64` を指す。wheel をダウンロードする 2 ステップは `matrix.platform.artifact` を使う
- `.github/workflows/test.yml` の `test_macos` の `platform` にも `artifact: macos-26_arm64` を追加し、ダウンロード手順を全ジョブで同じ形に揃える
- `README.md` の「プラットフォーム」に Ubuntu 26.04 LTS x86_64 / arm64 を追加する
- CI を実行し、26.04 x86_64 / arm64 の 3.14 / 3.14t の 4 ジョブが 24.04 ビルドの wheel を導入して成功することを確認する
