# ty が webtransport パッケージの型を Unknown として解決する問題を調査・修正する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
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
