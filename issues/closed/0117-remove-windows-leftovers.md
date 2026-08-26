# Windows 対応終了後に残ったビルド設定とコードの残骸を削除する

- Created: 2026-08-18
- Completed: 2026-08-26
- Branch: feature/remove-windows-leftovers
- Polished: {YYYY-MM-DD}

## 目的

CHANGES.md に「Windows 対応を終了する」と記載されているが、ビルド設定とコードに Windows 用の分岐が残っている。到達不能な死コードのため削除し、記述と現状を整合させる。

## 現状

- `CMakeLists.txt` は冒頭のプラットフォームチェックで非 Apple / 非 UNIX を FATAL_ERROR にするため、以下の WIN32 分岐は全て到達不能:
  - AWS-LC の .lib パス分岐 (MSVC マルチ構成ジェネレータ対応)
  - `CMAKE_MSVC_RUNTIME_LIBRARY` 設定
  - ws2_32 / advapi32 リンク
  - nghttp2 の `-DENABLE_STATIC_CRT`
  - MSVC_RUNTIME_LIBRARY / `/utf-8` / `/bigobj` / `_WIN32_WINNT` 設定ブロック
  - `if(NOT WIN32)` 分岐 (常に真)
- `src/bindings/http2.h` と `src/bindings/webtransport_h2.h` の `#ifdef _WIN32` による ssize_t 定義 (POSIX では不要)

## 設計方針

- `CMakeLists.txt` から WIN32 / MSVC 分岐を削除し、`if(NOT WIN32)` は条件を外して常時適用に簡略化する
- 両ヘッダーの `_WIN32` による ssize_t 定義を削除する

## 完了条件

- `CMakeLists.txt` に WIN32 / MSVC 分岐がなくなる
- `src/bindings/http2.h` / `src/bindings/webtransport_h2.h` に `_WIN32` 分岐がなくなる
- ビルドが通る

## 解決方法

- `CMakeLists.txt` から WIN32 / MSVC 分岐を削除し、POSIX 用の静的ライブラリパスと `-fvisibility=hidden` を常時適用にした
- `src/bindings/http2.h` と `src/bindings/webtransport_h2.h` の `_WIN32` による ssize_t 定義を削除した
- `make develop` でビルドが通ることを確認した
