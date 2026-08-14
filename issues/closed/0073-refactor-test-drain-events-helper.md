# テストのイベント取り出しヘルパー _drain_events を conftest.py に集約する

- Created: 2026-08-12
- Completed: 2026-08-14
- Branch: feature/refactor-test-drain-events-helper
- Polished: 2026-08-14

## 目的

同一実装の `_drain_events` (イベントキューを全て取り出すヘルパー) がテストファイル 12 件で重複している。イベントの取り出し仕様 (例: キューが空になるまで取り出す) を変更するときに全ファイルの修正が必要になるため、`tests/conftest.py` に集約して保守性を高める。closed issue 0028 で接続ヘルパー (`_pump` / `_create_session_pair` 等) を conftest.py に集約した流れの継続であり、0028 の集約対象に `_drain_events` は含まれていなかった。

## 現状

- 以下の 12 ファイルに `_drain_events` が定義されている (いずれも `next_event()` が `None` を返すまで取り出す同一実装。docstring の表現 (「コネクションのイベント」「セッションに積まれたイベント」等) と仮引数名 (conn / session) がファイル間で異なる):
  - `tests/test_http2_message_ext.py` (`http2.Connection` → `http2.Event`)
  - `tests/test_http2_session_control.py`
  - `tests/test_webtransport_h2_datagram.py` (`h2.Session` → `h2.Event`)
  - `tests/test_webtransport_h2_reject_session.py`
  - `tests/test_webtransport_h2_end_stream.py`
  - `tests/test_webtransport_h3_datagram.py` (`h3.Session` → `h3.Event`)
  - `tests/test_webtransport_h3_ghost_stream.py`
  - `tests/test_webtransport_h3_pre_accept_fin.py`
  - `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py`
  - `tests/test_webtransport_h3_reject_session.py`
  - `tests/test_webtransport_h3_server_reject_session.py`
  - `tests/test_webtransport_h3_stream_buffer_cleanup.py`
- `tests/conftest.py` には `_drain_events` は定義されていない
- `http2` 系の 2 ファイル (`tests/test_http2_message_ext.py` / `tests/test_http2_session_control.py`) は現状 conftest から import しておらず、この 2 ファイルには `from conftest import ...` の追加が必要になる (他の 10 ファイルは既に conftest から import している)
- 型の異なるセッション (h3.Session / h2.Session / http2.Connection) を 1 つのヘルパーで扱う必要があるため、型アノテーションの扱いが論点になる

## 設計方針

- `tests/conftest.py` に `_drain_events` を定義し、上記 12 ファイルの重複定義を削除して import に置き換える (0028 の `_pump` と同じ `from conftest import ...` の流儀。ヘルパー名は既存の `_drain_events` を維持する)
- 型アノテーションは TypeVar + Protocol による抽象化で表現する (呼び出し側で戻り値の要素型が正確に推論されるため):
  ```python
  from typing import Protocol, TypeVar

  E = TypeVar("E")

  class _EventSource(Protocol[E]):
      """next_event() でイベントを 1 件ずつ取り出せるオブジェクト (h3.Session / h2.Session / http2.Connection)"""

      def next_event(self) -> E | None: ...

  def _drain_events(source: _EventSource[E]) -> list[E]:
      """イベントを全て取り出す (next_event() が None を返すまで)"""
      events = []
      while True:
          event = source.next_event()
          if event is None:
              break
          events.append(event)
      return events
  ```
  Union 案 (`list[h3.Event] | list[h2.Event] | list[http2.Event]`) は戻り値の要素型が union に崩れ、呼び出し側のイベントタイプ比較 (`e.type == h3.EventType.SESSION_CLOSED` 等) で型が混在するため不採用。Protocol 案は構造的部分型のため conftest.py への `http2` の import 追加も不要。なお tests/ は ty チェック対象外 (prek.toml の `uv run ty check src`) のため CI には影響しないが、テストの可読性のため正確な型付けとする
- 各テストファイルの `_drain_events` の docstring の表現差は、conftest.py の定義に置く docstring「イベントを全て取り出す (`next_event()` が `None` を返すまで)」に統一する
- 変更対象は `tests/conftest.py` (`_drain_events` の追加)、上記 12 テストファイル (重複定義の削除と import への置き換え)、`CHANGES.md` (## develop セクションの misc への [UPDATE] エントリ。0028 の「WebTransport over HTTP/3 テストの接続ヘルパーを conftest.py に集約する」エントリの流儀に倣う)
- 実装順序によるマージの競合に注意する: 同一テストファイル群に触れる open issue が複数あるため、対象一覧の 12 ファイルが実装時点で変わり得る (0074 は test_webtransport_h2_datagram.py / test_webtransport_h2_end_stream.py、0072 は test_webtransport_h2_reject_session.py 等の h2 系テスト、0075 は h3 系の新規テスト追加の可能性。0028 が 0026 との競合に言及していた流儀に倣う)

## 完了条件

- 上記 12 ファイルの `_drain_events` の重複定義が削除され、全て conftest.py のヘルパーを使う
- 全テストが通る

## 解決方法

テストのイベント取り出しヘルパー `_drain_events` を `tests/conftest.py` に集約した。

- `tests/conftest.py` に `_drain_events` を追加した。型アノテーションは PEP 695 の型パラメータと `Protocol` による構造的部分型で抽象化し、`h3.Session` / `h2.Session` / `http2.Connection` のいずれも 1 つのヘルパーで扱えるようにした
- 重複定義を持つ 12 テストファイル (`tests/test_http2_message_ext.py` / `tests/test_http2_session_control.py` / `tests/test_webtransport_h2_datagram.py` / `tests/test_webtransport_h2_end_stream.py` / `tests/test_webtransport_h2_reject_session.py` / `tests/test_webtransport_h3_datagram.py` / `tests/test_webtransport_h3_ghost_stream.py` / `tests/test_webtransport_h3_pre_accept_fin.py` / `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` / `tests/test_webtransport_h3_reject_session.py` / `tests/test_webtransport_h3_server_reject_session.py` / `tests/test_webtransport_h3_stream_buffer_cleanup.py`) の重複定義を削除し、`from conftest import _drain_events` に置き換えた。conftest から import していなかった http2 系の 2 ファイルにも import を追加した
- docstring の表現差 (「コネクションのイベント」「セッションに積まれたイベント」等) は「イベントを全て取り出す (`next_event()` が `None` を返すまで)」に統一した

テスト本体 (各テスト関数のロジックとアサーション) は変更していない。全テスト (613 件) が通ることを確認済み。
