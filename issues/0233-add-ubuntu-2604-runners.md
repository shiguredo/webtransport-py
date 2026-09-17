# ubuntu-26.04 と ubuntu-26.04-arm を対応プラットフォームに追加する

- Created: 2026-09-17
- Completed: {YYYY-MM-DD}
- Branch: feature/add-ubuntu-2604-runners
- Polished: {YYYY-MM-DD}

## 目的

GitHub Actions で Ubuntu 26.04 LTS (x86_64 / arm64) の runner が利用できるようになったため、ビルドとテストの対象に追加する。対応プラットフォームの記載と実際に配布する wheel を一致させ、利用者が Ubuntu 26.04 環境でも wheel を導入できるようにする。

## 現状

- `.github/workflows/wheel.yml` の `build_ubuntu` は `ubuntu-24.04` / `ubuntu-24.04-arm` の 2 プラットフォームで wheel をビルドし、`publish_wheel` も同じ 2 プラットフォームを PyPI へ公開する
- `.github/workflows/test.yml` の `test_ubuntu` は同じ 2 プラットフォームで wheel を導入してテストする
- `README.md` の「プラットフォーム」は Ubuntu 24.04 LTS x86_64 / arm64 と macOS 26 arm64 のみを挙げている
- 他の時雨堂プロジェクト (aom-rs / dav1d-rs など) の CI は `ubuntu-26.04` / `ubuntu-26.04-arm` を使っており、本プロジェクトだけが 24.04 に留まっている

## 設計方針

- 24.04 は残し、26.04 を追加する (両方の LTS を配布対象にする)
- プラットフォーム名は既存の `ubuntu-<version>_<arch>` の規則に揃え、`ubuntu-26.04_x86_64` / `ubuntu-26.04_arm64` とする
- wheel のプラットフォームタグはビルド元の glibc に応じて auditwheel が付与するため、26.04 でビルドした wheel は 24.04 のものとは別のタグになる。両方を公開してよい
- ブラウザ E2E (`.github/workflows/e2e-test.yml`) は macOS のみで実行する検証であり、本 issue では変更しない

## 完了条件

- `.github/workflows/wheel.yml` の `build_ubuntu` と `publish_wheel` が `ubuntu-26.04` / `ubuntu-26.04-arm` を含む
- `.github/workflows/test.yml` の `test_ubuntu` が `ubuntu-26.04` / `ubuntu-26.04-arm` を含む
- `README.md` の「プラットフォーム」に Ubuntu 26.04 LTS x86_64 / arm64 が含まれる
- GitHub Actions で 26.04 のジョブが成功し、4 プラットフォーム分の wheel がアップロードされる

## 解決方法

- `.github/workflows/wheel.yml` の `build_ubuntu` と `publish_wheel` の `platform` に `ubuntu-26.04_x86_64` / `ubuntu-26.04_arm64` を追加する
- `.github/workflows/test.yml` の `test_ubuntu` の `platform` に同じ 2 つを追加する
- `README.md` の「プラットフォーム」に Ubuntu 26.04 LTS x86_64 / arm64 を追加する
