# nanobind 生成の src/webtransport/__init__.pyi の import 順が非ソートのまま残る問題を解消する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-pyi-import-order
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/__init__.pyi` に残る I001 (import ブロックの非ソート) を解消し、lint エラー 0 件の状態を生成物も含めて完全なものにする。0083 で lint エラー 10 件を修正したが、この生成物の違反は手修正できないため残っていた。

## 現状

- `src/webtransport/__init__.pyi` の import は `quic` → `http2` → `http3` → `h3` → `h2` の並びで、アルファベット順ではなく I001 違反 (`ruff check --select I001` で検出される)
- このファイルは nanobind が生成し、`make develop` が `_build/__init__.pyi` からコピーする生成物で、`.gitignore` の `*.pyi` で追跡対象外。手で直しても `make develop` で上書きされるため修正不能
- 他の生成 .pyi (`quic.pyi` / `http2.pyi` / `http3.pyi` / `h3.pyi`) に同種の違反があるかは未確認

## 設計方針

- 対応方法を調査して決める (案):
  - nanobind の .pyi 生成を制御してソート済みの出力を得る (生成オプション・後処理スクリプト)
  - `make develop` のコピー後に ruff の自動修正を挟む
  - lint 対象から `*.pyi` を除外して非対象にする (ただし「lint 0 件」の意味が変わるため、除外するなら理由を文書化する)
- どの .pyi に同種の違反があるか (`ruff check --select I001 src/**/*.pyi`) を確認して対象を特定する
- 変更対象: `Makefile` (生成後処理) / nanobind の .pyi 生成設定 / ruff 設定のいずれか / `CHANGES.md` (## develop の `### misc` への [UPDATE] エントリ)

## 完了条件

- `src/webtransport/__init__.pyi` (および同種の違反がある他の生成 .pyi) が I001 を検出しない状態になる (ソートされるか、lint 対象外として明文化される)
- `make develop` で再生成しても違反が復活しない
