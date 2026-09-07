# examples/http2/server.py が参照する http2.ResponseWriter が公開されておらず import 名として解決できない

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-reexport-response-writer
- Polished: 2026-09-07

## 目的

`examples/http2/server.py` の `on_request(stream_id: int, headers: list[tuple[str, str]], response_writer: http2.ResponseWriter)` は `webtransport.http2` から `ResponseWriter` を参照するが、`src/webtransport/http2/__init__.py` の `__all__` に `ResponseWriter` は含まれておらず、`webtransport.http2` の属性としては解決できない。Python 3.14 の PEP 649 遅延評価で実行時には落ちないだけで、型検査では `unresolved-attribute` になり、動的属性アクセスも失敗する。SKILL.md の回避策注記は「再エクスポートされていない。`from webtransport.http2.server import ResponseWriter` を使う」と案内するが、 examples はそれに従わず自己矛盾。対称の `h2.SessionWriter` は `webtransport.h2` から再エクスポート済み。

## 現状

- `src/webtransport/http2/__init__.py` の `__all__` は `["Client", "Config", "Connection", "Event", "EventType", "Server", "get_version", "select_alpn"]` (ResponseWriter 無し)
- `src/webtransport/http2/server.py` の `ResponseWriter` クラス定義あり
- `examples/http2/server.py` の `on_request` 内 `response_writer` 型注釈が `response_writer: http2.ResponseWriter`
- ty で `error[unresolved-attribute]` (兄弟 .pyi を退避したレイアウトで実測済み)
- 実行時: PEP 649 で型注釈が文字列として遅延評価されるため import エラーは起きないが、`hasattr(http2, 'ResponseWriter')` は False
- 対照: `src/webtransport/h2/__init__.py` は `from webtransport.h2.server import Server, SessionWriter` で `SessionWriter` を再エクスポート
- SKILL.md の ResponseWriter 回避策注記「`http2.ResponseWriter` は `webtransport.http2` から再エクスポートされていない。`from webtransport.http2.server import ResponseWriter` を使う」

## 設計方針

- `src/webtransport/http2/__init__.py` の import 行を `from webtransport.http2.server import ResponseWriter, Server` の一行結合 (isort 順。h2 側 `SessionWriter` と対称) に変え、`__all__` に `ResponseWriter` をソート順で含める
- SKILL.md の ResponseWriter 回避策注記の bullet 全体を削除する (直後の `CapsuleType` 注記は残す)
- `examples/http2/server.py` の import が現状の `from webtransport import http2` で動作することを確認する

## 完了条件

- `hasattr(http2, 'ResponseWriter')` が True になること
- 兄弟 `.pyi` を退避したレイアウトで `ty check examples/http2/server.py` を実行し、`unresolved-attribute` が 0 件になること (`.pyi` はビルド生成物のため本 issue では更新しない)
- SKILL.md から回避策注記が削除されていること
- 既存のテスト全 834 件が引き続き通過すること
