# ty が webtransport パッケージの型を Unknown として解決する問題を調査・修正する

- Created: 2026-08-14
- Completed: 2026-09-07
- Branch: feature/refactor-webtransport-type-resolution
- Polished: {YYYY-MM-DD}

## 目的

静的型チェッカ ty が webtransport パッケージの公開型 (h3.Session 等) を `Unknown` として解決しており、型チェックが実質機能していない。型注釈の正確性を担保するため、解決方法を調査して修正する。

## 現状

- `uv run ty check` で `from webtransport import h3; h3.Session.create_client(...)` の型を reveal_type で確認すると `Unknown` になる (実測確認済み)
- `h3` / `h2` はディレクトリパッケージ (`src/webtransport/h3/__init__.py` 等) で、C 拡張 `webtransport.webtransport_ext.h3` からの re-export をしている
- 型スタブ (`src/webtransport/h3.pyi` 等) は存在するが、ディレクトリパッケージの型として ty に解決されていない。C 拡張側も `src/webtransport/webtransport_ext.pyi` が `h3` 等のサブモジュールを import するだけで、サブモジュールの型スタブが存在しない
- 影響: `tests/` は ty チェック対象外だが、パッケージ自身の Python コード (`src/webtransport/h3/server.py` 等) の型検証も webtransport 型の部分だけ `Unknown` になり空振りする
- `uv run ty check src` はエラーなしで通る (`Unknown` はエラーにならないため、問題が顕在化していない)

## 設計方針

- まず型スタブの配置 (例: `h3.pyi` を `h3/__init__.pyi` に置く、C 拡張サブモジュールの型スタブを用意する) で ty が解決できるか調査する
- 公開型 (h3.Session / h2.Session / http2.Connection / http3.Connection 等) が ty で正しい型に解決されることを reveal_type で確認する
- 修正範囲・方法が確定したら実装する (調査から始める)

## 完了条件

- ty で webtransport パッケージの公開型が `Unknown` にならず、正しい型に解決される
- `uv run ty check src` が webtransport 型に対して機能していることを reveal_type で確認できる

## 解決方法

issue 0167 (`refactor-type-stubs-package-layout`) に統合して close する。

本 issue の「現状」節の前提「静的型チェッカ ty が webtransport パッケージの公開型 (h3.Session 等) を `Unknown` として解決」は、3 周目の実測 (`uvx ty@0.0.78 check` + `reveal_type` プローブ) で誤りと判明した。実際の解決状況は以下:

- 解決できる (Sans-IO 低レベル型): `h3.Session` は `<class 'Session'>`、`h3.Session.create_client` は `def create_client(config: Config) -> Session`、`quic.Connection` は `<class 'Connection'>`
- 解決できない (高レベル型): `h3.Client` / `h3.Server` / `h2.Client` / `h2.Server` / `quic.Client` / `SessionWriter` / `ResponseWriter` / `WebTransportConnectError` 等の 5 モジュールの Client / Server / 例外群が `Unknown` + `error[unresolved-attribute]`

真の根本原因は本 issue の想定 (「兄弟 `.pyi` がディレクトリパッケージの型として解決されていない」) の逆で、以下の 2 点:

1. `src/webtransport/webtransport_ext.pyi` の `from webtransport_ext import ...` が存在しない最上位モジュールを参照する壊れた import になっており、低レベル型経路を実質破壊している (これを `unresolved-import = "ignore"` で全面的に隠している)
2. 兄弟 `.pyi` が PEP 561 の解決順で `__init__.py` を優先的に隠す配置

これらは issue 0167 の設計方針 (スタブパッケージ化 = `webtransport/webtransport_ext/__init__.pyi` + サブモジュール `.pyi` 5 本 + `unresolved-import = "ignore"` 除去) で根本的に解消される。0167 の完了条件が本 issue の完了条件を包含するため、本 issue を close して 0167 に一本化する。
