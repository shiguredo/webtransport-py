# 型スタブをスタブパッケージ化して高レベル Client / Server / 例外を型検査に露出する

- Created: 2026-09-07
- Completed: 2026-09-13
- Branch: feature/refactor-type-stubs-package-layout
- Polished: {YYYY-MM-DD}

## 目的

現状の型スタブ配置 (兄弟 `.pyi` + フラット `webtransport_ext.pyi`) は PEP 561 の解決順で `__init__.py` を隠す。結果、5 モジュール全部で `Client` / `Server` / `SessionWriter` / `ResponseWriter` と例外 4 種が型検査から消え、`pyproject.toml` の `[tool.ty.rules] unresolved-import = "ignore"` で全面的に見えなくされている。実測 (`uvx ty@0.0.78 check`) で examples 16 エラー、tests 62 エラーが潜在。スタブパッケージ化すれば `unresolved-import` の抑止を外しても src が 0 エラーになることを scratchpad で実測済み。既存 issue 0077 は「`h3.Session` が Unknown」を前提としているが実測では逆で、隠れているのは高レベル `Client` / `Server` 側であり、根本原因は本 issue の配置問題。

## 現状

- `Makefile:12-16` `develop` ターゲット: `_build/h3.pyi` / `_build/h2.pyi` を `src/webtransport/` 直下に、`_build/quic.pyi` / `http2.pyi` / `http3.pyi` を `src/webtransport/quic/__init__.pyi` 等にコピー
- wheel の実配置 (`_build/install_manifest.txt` から推定): `webtransport/h2.pyi` / `h3.pyi` / `quic.pyi` 等の兄弟 `.pyi` + `webtransport/__init__.pyi` (CMake `file(WRITE)` 生成)
- `src/webtransport/webtransport_ext.pyi` の `from webtransport_ext import (h2, h3, http2, http3, quic)` は存在しない最上位モジュール `webtransport_ext` を import (実行時は ModuleNotFoundError にはならないが型解決不能)
- 実測 (兄弟 `.pyi` あり): `error[unresolved-import]` 6 件 (5 モジュールの `webtransport.webtransport_ext.<mod>` + `webtransport_ext.pyi` 自身)
- 実測 (兄弟 `.pyi` を退避): `error[unresolved-import]` 15 件
- 実測 (スタブパッケージ化: `webtransport/webtransport_ext/__init__.pyi` + 5 本): エラー 0 件 (`--warn all` で `unsound-return-statement` 3 件のみ)
- CI の prek `ty` は `[tool.ty.src] include = ["src"]` で tests / examples を検査していない (実測 examples 16 エラー、tests 62 エラーが潜在)
- 既存 issue: 0077 「ty が webtransport パッケージの型を Unknown として解決する問題を調査・修正する」は前提が逆 (実測で Unknown なのは高レベル `Client` 側)
- 既存 issue: 0088 「nanobind 生成の src/webtransport/__init__.pyi の import 順が非ソートのまま残る問題を解消する」は関連するが根本原因ではない

## 設計方針

- `webtransport_ext` をスタブパッケージ化する: `webtransport/webtransport_ext/__init__.pyi` に `from . import quic as quic, http2 as http2, ...` を書き、`webtransport/webtransport_ext/quic.pyi` / `http2.pyi` / `http3.pyi` / `h2.pyi` / `h3.pyi` の 5 本を配置する
- nanobind の `nanobind_add_stub` は単一ファイル出力のため、`python -m nanobind.stubgen -m webtransport_ext -r -O <dir>` の再帰オプションを `add_custom_command` で呼ぶ形に置き換える
- `CMakeLists.txt` の `__init__.pyi` 生成 (`:363-374`) と 6 本の兄弟スタブ (`:313-361, :384-393`) を廃止する
- `Makefile:12-16` の cp 列を新レイアウトに合わせて更新する
- 高レベルパッケージ (`quic/__init__.py` 等) は型付きソース自身を型情報源にする (`.pyi` を作らない)
- 変更後 `[tool.ty.rules] unresolved-import = "ignore"` を撤去する (既知の issue 0088 も自然に解消)
- `[tool.ty.src] include` に tests / examples を追加し、潜在 62 エラーを解消する (別 issue または本 issue の後段で対応)

## 完了条件

- ty が src / tests / examples を全て検査してエラー 0 件になること
- wheel と `make develop` のレイアウトが同一で、型検査結果が両者で一致すること
- `[tool.ty.rules] unresolved-import = "ignore"` を撤去できること
- 既存 issue 0077 と 0088 を closed にできる状態になること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

- `CMakeLists.txt` の 6 本の `nanobind_add_stub` と `file(WRITE)` による `__init__.pyi` 生成を廃止し、`nanobind_add_stub` 1 回 (RECURSIVE + INCLUDE_PRIVATE + OUTPUT_PATH) で `webtransport_ext` をスタブパッケージとして出力するようにした。出力は `_build/webtransport_ext/{__init__,quic,http2,http3,h3,h2}.pyi` の 6 本
- `Makefile` の `develop` を新レイアウトに合わせ、旧レイアウトの兄弟 `.pyi` (`src/webtransport/*.pyi` と `src/webtransport/{quic,http2,http3}/__init__.pyi`) を毎回削除してから `_build/webtransport_ext` を `src/webtransport/webtransport_ext` へコピーするようにした。兄弟 `.pyi` は PEP 561 の解決順でサブパッケージの `__init__.py` を隠すため、削除しないと高レベル API が型検査から消える
- `scripts/normalize_stubs.py` を追加し、生成物の絶対 import を相対 import へ書き換えて ruff で整形・ソートしたうえでスタブを追跡するようにした。追跡しないと CI の `prek` (`ty`) がビルド成果物の無いチェックアウトで `webtransport.webtransport_ext` を解決できず失敗する
- `Makefile` の `develop` は生成結果と追跡ファイルの `diff -r` を取り、スタブの更新漏れを検出する
- `pyproject.toml` の `[tool.ty.rules] unresolved-import = "ignore"` を撤去した。ty のエラーは 0 件
- `[tool.ty.src] include` を `["src", "examples"]` に広げた。examples は 0 エラー。tests は 57 件の潜在エラーがあり本 issue の範囲を超えるため別途対応とする
- wheel の中身が `webtransport/webtransport_ext/*.pyi` + `webtransport/py.typed` になり、`make develop` のレイアウトと一致することを確認した
- `webtransport` の高レベル API (`quic.Client` / `h3.Server` / `http2.ResponseWriter` / 例外 4 種など) が型検査から見えるようになったことを、リポジトリ外の型検査用ファイルで確認した
- 全テストが通ることを確認した
