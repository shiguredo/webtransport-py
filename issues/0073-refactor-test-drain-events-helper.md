# テストのイベント取り出しヘルパー _drain_events を conftest.py に集約する

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-test-drain-events-helper
- Polished: {YYYY-MM-DD}

## 目的

同一実装の `_drain_events` (イベントキューを全て取り出すヘルパー) がテストファイル 10 件で重複している。イベントの取り出し仕様 (例: キューが空になるまで取り出す) を変更するときに全ファイルの修正が必要になるため、`tests/conftest.py` に集約して保守性を高める。closed issue 0028 で接続ヘルパー (`_pump` / `_create_session_pair` 等) を conftest.py に集約した流れの継続であり、0028 の対象外だった `_drain_events` が残っている。

## 現状

- 以下の 10 ファイルに `_drain_events` が定義されている (いずれも `next_event()` が `None` を返すまで取り出す同一実装。型のみ異なる):
  - `tests/test_http2_message_ext.py` (`http2.Connection` → `http2.Event`)
  - `tests/test_http2_session_control.py`
  - `tests/test_webtransport_h2_datagram.py` (`h2.Session` → `h2.Event`)
  - `tests/test_webtransport_h3_datagram.py` (`h3.Session` → `h3.Event`)
  - `tests/test_webtransport_h3_ghost_stream.py`
  - `tests/test_webtransport_h3_pre_accept_fin.py`
  - `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py`
  - `tests/test_webtransport_h3_reject_session.py`
  - `tests/test_webtransport_h3_server_reject_session.py`
  - `tests/test_webtransport_h3_stream_buffer_cleanup.py`
- `tests/conftest.py` には `_drain_events` は定義されていない
- 型の異なるセッション (h3.Session / h2.Session / http2.Connection) を 1 つのヘルパーで扱う必要があるため、型アノテーションの扱いが論点になる

## 設計方針

- `tests/conftest.py` に `_drain_events` を定義し、上記 10 ファイルの重複定義を削除して import に置き換える (0028 の `_pump` と同じ `from conftest import ...` の流儀)
- 型アノテーションは 10 ファイル全てで同じ取り出し処理が使える範囲で表現する (例: 戻り値は `list[h3.Event] | list[h2.Event] | list[http2.Event]` か、取り出す対象を抽象化する)
- 各テストファイルの `_drain_events` の docstring の表現差 (「コネクションのイベント」「セッションに積まれたイベント」等) は統一する

## 完了条件

- 上記 10 ファイルの `_drain_events` の重複定義が削除され、全て conftest.py のヘルパーを使う
- 全テストが通る
