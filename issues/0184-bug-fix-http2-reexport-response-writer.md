# examples/http2/server.py が参照する http2.ResponseWriter が公開されておらず import 名として解決できない

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-reexport-response-writer
- Polished: {YYYY-MM-DD}

## 目的

`examples/http2/server.py` の `on_request(stream_id: int, headers: list[tuple[str, str]], response_writer: http2.ResponseWriter)` は `webtransport.http2` から `ResponseWriter` を参照するが、`src/webtransport/http2/__init__.py` の `__all__` に `ResponseWriter` は含まれておらず、`webtransport.http2` の属性としては解決できない。Python 3.14 の PEP 649 遅延評価で実行時には落ちないだけで、型検査では `unresolved-attribute` になり、動的属性アクセスも失敗する。SKILL.md:662 は「再エクスポートされていない。`from webtransport.http2.server import ResponseWriter` を使う」と回避策を案内するが、examples はそれに従わず自己矛盾。対称の `h2.SessionWriter` は `webtransport.h2` から再エクスポート済み。

## 現状

- `src/webtransport/http2/__init__.py:34-43` の `__all__` は `["Client", "Config", "Connection", "Event", "EventType", "Server", "get_version", "select_alpn"]` (ResponseWriter 無し)
- `src/webtransport/http2/server.py:18` に `class ResponseWriter` の定義あり
- `examples/http2/server.py:23` の型注釈が `response_writer: http2.ResponseWriter`
- ty で `error[unresolved-attribute]` (兄弟 .pyi を退避したレイアウトで実測済み)
- 実行時: PEP 649 で型注釈が文字列として遅延評価されるため import エラーは起きないが、`hasattr(http2, 'ResponseWriter')` は False
- 対照: `src/webtransport/h2/__init__.py` は `from webtransport.h2.server import Server, SessionWriter` で `SessionWriter` を再エクスポート
- SKILL.md:662 「`http2.ResponseWriter` は `webtransport.http2` から再エクスポートされていない。`from webtransport.http2.server import ResponseWriter` を使う」

## 設計方針

- `src/webtransport/http2/__init__.py` に `from webtransport.http2.server import ResponseWriter` を追加し `__all__` に `ResponseWriter` を含める (h2 側 `SessionWriter` と対称)
- SKILL.md:662 の「再エクスポートされていない」注記を削除する
- `examples/http2/server.py` の import が現状の `from webtransport import http2` で動作することを確認する

## 完了条件

- `hasattr(http2, 'ResponseWriter')` が True になること
- `examples/http2/server.py` が型検査を通過すること
- SKILL.md から回避策注記が削除されていること
- 既存のテスト全 822 件が引き続き通過すること
