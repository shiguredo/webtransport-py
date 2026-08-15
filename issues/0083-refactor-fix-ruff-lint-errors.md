# ruff の lint エラー 10 件を修正する

- Created: 2026-08-15
- Completed: 2026-08-15
- Branch: feature/refactor-fix-ruff-lint-errors
- Polished: 2026-08-15

## 目的

`uv run ruff check src/ tests/ examples/` が報告する 10 件の lint エラーを修正し、`make lint` を成功させる。CI は lint を実行していないため現状検知されないが、prek の ruff-check フック (`prek.toml` の `entry = "uv run ruff check"`) が Python ファイルのコミットをブロックするため、対象ファイルに触れる変更が現にできない状態にある (「Don't live with broken windows」の原則にも反する)。CI への lint 追加は本 issue のスコープ外とし、別途検討する。

## 現状

- エラーは 10 件 (I001 6 件 / RUF022 2 件 / PYI034 2 件):
  - `src/webtransport/__init__.py` の import ブロックのソート (I001) と `__all__` のソート (RUF022)
  - `src/webtransport/h3/__init__.py` の import ブロックのソート (I001) と `__all__` のソート (RUF022)
  - `src/webtransport/http2/client.py` の `__aenter__` の戻り値型 (PYI034: `Self` を使う)
  - `src/webtransport/http2/server.py` の `__aenter__` の戻り値型 (PYI034: `Self` を使う)
  - `tests/prop_http2.py` / `tests/prop_http3.py` / `tests/prop_isolation_h2.py` / `tests/prop_webtransport_h2.py` の import ブロックのソート (I001)

## 設計方針

- I001 / RUF022 の 8 件は `uv run ruff check --fix` で自動修正する (import ブロックの並べ替え・1 行化・空行調整を伴うが、実行時挙動は変わらない)
- PYI034 の 2 件は `__aenter__` の戻り値型を `Self` に書き換える。自動修正 (unsafe-fix) は型注釈の意味を変える変更 (`Self` はサブクラス時の型推論が変わる) のため手動で直し、import は既存の `from typing import TYPE_CHECKING, Self` の形式 (h2 / h3 / http3 / quic の client / server に先例あり) に合わせて追加する。挙動は変わらない
- 修正後に `uv run ruff check src/ tests/ examples/` がエラー 0 件になることを確認する
- 変更対象: 上記 8 ファイル / `CHANGES.md` (## develop セクションの `### misc` への [UPDATE] エントリ)。`src/webtransport/h3/__init__.py` は open issue 0077 の型解決方法の調査対象にも含まれるため、実装順序によるマージの競合に注意する (0028 の前例に倣う)

## 完了条件

- `uv run ruff check src/ tests/ examples/` がエラー 0 件で通る
- `uv run pytest tests/ -v --timeout=30` が通る
- `CHANGES.md` の `### misc` に [UPDATE] エントリが追加されている

## 解決方法

- I001 / RUF022 の 8 件は `uv run ruff check --fix` で自動修正した (import ブロックの並べ替え・1 行化・空行調整・`__all__` のソート。実行時挙動は変わらないことを全テストで確認)
- PYI034 の 2 件は `src/webtransport/http2/client.py` / `server.py` の `__aenter__` の戻り値型を `Self` に手動で書き換え、`from typing import TYPE_CHECKING, Self` の形式に import を揃えた (h2 / h3 / http3 / quic の先例と一致。挙動は変わらない)
- `CHANGES.md` の `### misc` に [UPDATE] エントリを追加した
- `uv run ruff check src/ tests/ examples/` がエラー 0 件、`uv run ty check src` がパス、全テスト (647 本) が通ることを確認した
