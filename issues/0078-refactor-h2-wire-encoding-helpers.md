# h2 テストのワイヤ組み立てヘルパーを conftest.py に集約する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h2-wire-encoding-helpers
- Polished: 2026-09-05

## 目的

同一実装または同型の h2 用ワイヤ組み立てヘルパーが複数のテストファイルで重複している。ワイヤ形式 (Capsule Protocol RFC 9297 Section 3.2 のカプセル組み立て、HTTP/2 DATA フレームの組み立て。RFC 9297 本体は `refs/` 未収録のため実装時に一次資料を確認する。テスト本体の docstring の引用と整合させる) を変更するときに全ファイルの修正が必要になるため、`tests/conftest.py` に集約して保守性を高める。イベント取り出しヘルパー `_drain_events` の conftest.py 集約 (closed issue 0073) の流れの継続である。

## 現状

- `_encode_capsule` が h2 系 9 ファイルに重複定義されている (`tests/test_webtransport_h2_datagram.py` / `test_webtransport_h2_end_stream.py` / `test_webtransport_h2_reject_session.py` / `test_webtransport_h2_close_session.py` 系を含む検証系 6 ファイル。H3 系の同名ヘルパーはシグネチャが異なるため対象外)。実装は 2 系統ある。1 バイト varint 版と汎用 varint (`_encode_varint` 利用) 版であり、集約時は汎用版に統一する
- `_encode_data_frame` が h2 系 11 ファイルに重複定義されている。シグネチャは 3 系統 (`end_stream` 付き / `payload` 必須フラグなし / `payload` 既定フラグなし) が混在し、集約時は `end_stream` 付きに統一する
- 単一ファイルのみのヘルパー: `_encode_headers_frame` (`tests/test_webtransport_h2_end_stream.py`)、`_encode_status_headers` (`tests/test_webtransport_h2_reject_session.py`。任意の `:status` を組み立てる汎用ヘルパー)
- `tests/conftest.py` に h2 用のワイヤ組み立てヘルパーは定義されていない
- 集約の先例: closed issue 0073 が `_drain_events` を conftest.py に集約した。同様に `_h2_pump` / `_create_h2_session_pair` / `_connect_h2_session` 等の h2 用 Sans-IO ヘルパーも conftest.py に集約済み

## 設計方針

- `tests/conftest.py` に `_encode_capsule` / `_encode_data_frame` を定義し、h2 系の重複ファイルのローカル定義を削除して `from conftest import ...` に置き換える (0073 の `_drain_events` と同じ流儀)。H3 系の同名ヘルパーは名前衝突を避けるため対象外とする
- ヘルパー名は既存名を維持し、実装は汎用版に統一する (`_encode_capsule` は汎用 varint 版、`_encode_data_frame` は `end_stream` 付き)。docstring は RFC 9297 Section 3.2 の引用を維持する
- `_encode_headers_frame` / `_encode_status_headers` は単一ファイルのみのため集約対象外とする
- 変更対象は `tests/conftest.py`、上記 h2 テストファイル、`CHANGES.md` (## develop セクションの misc への [UPDATE] エントリ。0073 のエントリの流儀に倣う)
- `tests/conftest.py` を変更する open の 0076 / 0081 とはマージ順序を調整する

## 完了条件

- h2 系の `_encode_capsule` / `_encode_data_frame` の重複定義が削除され、対象ファイルが全て conftest.py のヘルパーを使う
- 全テストが通る
