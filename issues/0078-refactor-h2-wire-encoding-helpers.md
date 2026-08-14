# h2 テストのワイヤ組み立てヘルパーを conftest.py に集約する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h2-wire-encoding-helpers
- Polished: {YYYY-MM-DD}

## 目的

同一実装の h2 用ワイヤ組み立てヘルパーが複数のテストファイルで重複している。ワイヤ形式 (Capsule Protocol RFC 9297 Section 3.2 のカプセル組み立て、HTTP/2 DATA フレームの組み立て) を変更するときに全ファイルの修正が必要になるため、`tests/conftest.py` に集約して保守性を高める。イベント取り出しヘルパー `_drain_events` の conftest.py 集約 (closed issue 0073) の流れの継続である。

## 現状

- `_encode_capsule` (Capsule Protocol のカプセルバイト列を組み立てる。Type / Length とも 1 バイト varint) が 3 ファイルに同一実装で定義されている:
  - `tests/test_webtransport_h2_datagram.py`
  - `tests/test_webtransport_h2_end_stream.py`
  - `tests/test_webtransport_h2_reject_session.py`
- `_encode_data_frame` (HTTP/2 DATA フレームのワイヤバイト列を組み立てる。END_STREAM フラグ対応) が 2 ファイルに同一実装で定義されている:
  - `tests/test_webtransport_h2_end_stream.py`
  - `tests/test_webtransport_h2_reject_session.py`
- 単一ファイルのみのヘルパー: `_encode_headers_frame` (test_webtransport_h2_end_stream.py)、`_encode_1xx_headers` (test_webtransport_h2_reject_session.py)
- `tests/conftest.py` に h2 用のワイヤ組み立てヘルパーは定義されていない
- 集約の先例: closed issue 0073 が `_drain_events` を conftest.py に集約した。同様に `_pump` / `_create_h2_session_pair` / `_connect_h2_session` 等の h2 用 Sans-IO ヘルパーも conftest.py に集約済み

## 設計方針

- `tests/conftest.py` に `_encode_capsule` / `_encode_data_frame` を定義し、重複ファイルのローカル定義を削除して `from conftest import ...` に置き換える (0073 の `_drain_events` と同じ流儀)
- ヘルパー名・docstring は既存の実装を維持する
- `_encode_headers_frame` / `_encode_1xx_headers` は単一ファイルのみのため集約対象外とする
- 変更対象は `tests/conftest.py`、上記テストファイル、`CHANGES.md` (## develop セクションの misc への [UPDATE] エントリ。0073 のエントリの流儀に倣う)

## 完了条件

- `_encode_capsule` / `_encode_data_frame` の重複定義が削除され、全て conftest.py のヘルパーを使う
- 全テストが通る
