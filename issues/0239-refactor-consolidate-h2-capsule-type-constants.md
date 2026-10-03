# h2 のテストでカプセル種別の定数が複数ファイルに重複定義されているのを集約する

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-h2-capsule-type-constants
- Polished: 2026-10-03

## 目的

h2 のテストでは、WebTransport カプセル種別の数値 (draft-15 Section 6 の
WT_RESET_STREAM / WT_STOP_SENDING / WT_STREAM / WT_STREAM_FIN / WT_MAX_DATA /
WT_MAX_STREAM_DATA / WT_MAX_STREAMS / WT_DATA_BLOCKED / WT_STREAM_DATA_BLOCKED /
WT_STREAMS_BLOCKED / WT_PADDING、draft-15 Section 6.12 / 6.13 が参照する
draft-ietf-webtrans-http3-16 の WT_CLOSE_SESSION / WT_DRAIN_SESSION、RFC 9297
Section 3.5 の DATAGRAM) が、テストファイルごとのローカル `_WT_*` 定数、
`_encode_*` ヘルパ内の手組みバイト列、生リテラルとして多数重複している。同じ
数値が複数ファイルに散らばっているため、カプセル種別を追加・変更したときの
追従漏れと、レビュー時の突き合わせコストが生じる。`tests/conftest.py` の
`_encode_wt_max_stream_data_capsule` / `_encode_wt_stream_data_blocked_capsule`
も種別の数値を内部に持つため、テストファイルから種別の値として利用できない。
エラーコードは `WtErrorCode` を単一の出典とする形に既に改められており
(`tests/test_webtransport_h2_flow_control_capsule.py` の `WT_FLOW_CONTROL_ERROR`
等)、カプセル種別も同じ方向性で h2 テスト全体の出典を 1 箇所に集約する。

## 現状

- `tests/conftest.py` の 2 エンコーダは種別の数値を内部に持つ
  - `_encode_wt_max_stream_data_capsule` は 0x190B4D3E を内部に持つ
  - `_encode_wt_stream_data_blocked_capsule` は 0x190B4D42 を内部に持つ
  - どちらも `_encode_capsule(種別, ペイロード)` を包む形であり、種別を引数で
    受け取らないためテストから定数として使えない
- `_WT_CLOSE_SESSION_TYPE_BYTES = _encode_varint(0x2843)` も種別の数値を
  リテラルで持つ (既に 3 テストがこの定数を import している)
- 以下のテストファイルがそれぞれローカル `_WT_*` 定数を定義している (定義
  ファイルは種別によって 1 から 6 ファイル)
  - `_WT_MAX_STREAM_DATA` = 0x190B4D3E:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_initiator_validation.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py` /
    `tests/test_webtransport_h2_initial_flow_control_fallback.py`
  - `_WT_STREAM_DATA_BLOCKED` = 0x190B4D42:
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_MAX_DATA` = 0x190B4D3D:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py` /
    `tests/test_webtransport_h2_initial_flow_control_fallback.py`
  - `_WT_STOP_SENDING` = 0x190B4D3A:
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_received_map_bound.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_initiator_validation.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_DATA_BLOCKED` = 0x190B4D41:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_MAX_STREAMS_BIDI` = 0x190B4D3F と `_WT_MAX_STREAMS_UNI` = 0x190B4D40:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py` /
    `tests/test_webtransport_h2_initial_flow_control_fallback.py`
  - `_WT_STREAM` = 0x190B4D3C:
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_initiator_validation.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py` /
    `tests/test_webtransport_h2_received_map_bound.py` /
    `tests/test_webtransport_h2_recv_flow_control.py` /
    `tests/test_webtransport_h2_capsule_buffer_bound.py`
  - `_WT_STREAM_FIN` = 0x190B4D3B:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_flow_control_replenishment.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_RESET_STREAM` = 0x190B4D39:
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_initiator_validation.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_STREAMS_BLOCKED_BIDI` = 0x190B4D43 と `_WT_STREAMS_BLOCKED_UNI` =
    0x190B4D44:
    `tests/test_webtransport_h2_flow_control_capsule.py` /
    `tests/test_webtransport_h2_capsule_trailing_bytes.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_CLOSE_SESSION` = 0x2843:
    `tests/test_webtransport_h2_capsule_buffer_bound.py` /
    `tests/test_webtransport_h2_incomplete_capsule_payload.py`
  - `_WT_DRAIN_SESSION` = 0x78AE / `_WT_PADDING` = 0x190B4D38 /
    `_WT_DATAGRAM` = 0x00: `tests/test_webtransport_h2_capsule_trailing_bytes.py`
    のみの定義だが、同じ値は他のファイルで生リテラルとして使われている
- 生リテラルや手組みの Type バイト列は次のファイルに残る (抜粋)
  - `tests/test_webtransport_h2_close_session.py`: `_encode_capsule(0x190B4D42, ...)` /
    `_encode_capsule(0x190B4D3E, ...)` / `_encode_varint(0x2843)` /
    ヘルパ内の `b"\x68\x43"` 手組み
  - `tests/test_webtransport_h2_initial_flow_control_fallback.py`:
    `_encode_capsule(0x190B4D41, ...)` とヘルパ内の `b"\x68\x43"` 手組み
  - `tests/test_webtransport_h2_flow_control_replenishment.py`:
    `_encode_capsule(0x190B4D43, ...)` (2 箇所) / `_encode_capsule(0x190B4D39, ...)`
    (3 箇所) / `_encode_capsule(0x190B4D40, ...)` (2 箇所) / `b"\x99\x0b\x4d\x3e"` /
    `b"\x68\x43"`
  - `tests/test_webtransport_h2_end_stream.py`: `_encode_varint(0x190B4D3D /
    0x190B4D3F / 0x190B4D40)` / `_encode_capsule(0x190B4D38, ...)` /
    `_encode_capsule(0x78AE, ...)` / `_encode_capsule(0x2843, ...)` /
    `_encode_capsule(0x00, ...)` (3 箇所) / `b"\x68\x43"` / `b"\x99\x0b\x4d\x3d"`
  - `tests/test_webtransport_h2_datagram.py`: `_encode_capsule(0x00, ...)` /
    `_encode_capsule(0x190B4D3C, ...)` / `_encode_capsule(0x2843, ...)` (2 箇所) /
    `_encode_capsule(0x190B4D3D, ...)`
  - `tests/test_webtransport_h2_error_code_range.py`: `_encode_capsule(0x190B4D39 /
    0x190B4D3A / 0x190B4D3B / 0x2843, ...)` と `b"\x68\x43"`
  - `tests/test_webtransport_h2_recv_stream_limit.py`: `_encode_capsule(0x190B4D39 / 0x2843, ...)`
  - `tests/test_webtransport_h2_recv_flow_control.py`: `_encode_capsule(0x2843, ...)` /
    `_encode_capsule(0x00, ...)` / `b"\x68\x43"`
  - `tests/test_webtransport_h2_close_session_message.py`: `_encode_capsule(0x2843, ...)`
    (2 箇所)
  - `tests/test_webtransport_h2_capsule_buffer_bound.py`: `_encode_capsule(0x00, ...)`
    (3 箇所)
  - `tests/test_webtransport_h2_reject_session.py`: `_encode_capsule(0x00, ...)`
    (5 箇所)
  - `tests/test_webtransport_h2_received_map_bound.py`: `b"\x68\x43"` (2 箇所)
  - `tests/test_webtransport_h2_stream_state_error.py` /
    `tests/test_webtransport_h2_send_stream_data_reset_stream.py` /
    `tests/test_webtransport_h2_reset_validation.py` /
    `tests/test_webtransport_h2_stop_sending_drain_session.py`:
    `_encode_wt_stream_capsule` / `_encode_wt_reset_stream_capsule` /
    `_encode_wt_stop_sending_capsule` 等のヘルパが `bytes([0x99, 0x0B, 0x4D, ...])`
    で種別を手組みしている (0x190B4D3C / 0x190B4D3B / 0x190B4D39 / 0x190B4D3A)
  - `tests/test_e2e_webtransport_h2.py`: `_encode_h2_wt_stream_data_frame` 内の
    `_encode_varint(0x190B4D3C)`
- 同じカプセルを「conftest のヘルパで組む」「自前の定数 + `_encode_capsule` で
  組む」「生リテラルで組む」の 3 通りが併存している。直近の追加でも、ある
  ファイルはヘルパ、別のファイルは自前定数という使い分けが生じた
  (`tests/test_webtransport_h2_stream_state_error.py` は
  `_encode_wt_max_stream_data_capsule` / `_encode_wt_stream_data_blocked_capsule` を
  import し、`tests/test_webtransport_h2_capsule_trailing_bytes.py` /
  `tests/test_webtransport_h2_incomplete_capsule_payload.py` はローカル定数を
  定義している)

## 設計方針

- カプセル種別の数値の出典は `tests/conftest.py` の `_WT_*` 定数 1 箇所にする
  (draft-15 Section 6 と RFC 9297 の値。既存エンコーダと同一の一次資料)。
  既存の `_WT_CLOSE_SESSION_TYPE_BYTES` は `_encode_varint(_WT_CLOSE_SESSION)` の
  形で定数から導出する
- `tests/conftest.py` の 2 エンコーダは内部で `_WT_*` 定数を使う形に書き換える。
  WT_MAX_STREAM_DATA / WT_STREAM_DATA_BLOCKED を直接組み立てるテストは
  このエンコーダを使う
- その他の種別は `_encode_capsule(_WT_*, ペイロード)` で組み立てる。テスト内の
  手組み Type バイト列 (`bytes([0x99, 0x0B, 0x4D, ...])` / `b"\x68\x43"`) と
  `_encode_varint(0x...)` の生リテラルは、`_encode_varint(_WT_*)` または
  `_encode_capsule(_WT_*, ...)` に置き換える
- テストの意図を読みにくくしないこと。`tests/test_webtransport_h2_flow_control_capsule.py`
  は非準拠値の注入を目的に `_inject_capsule(capsule_type, payload)` の形を取って
  おり、この形を維持するなら定数の import だけで済む。同様に
  `tests/test_webtransport_h2_initiator_validation.py` の `_inject` も形状を
  維持する
- 変更対象は `tests/conftest.py` と h2 系テストファイル
  (`tests/test_webtransport_h2_*.py` / `tests/test_e2e_webtransport_h2.py`) とする。
  h3 系・http3 系のテスト (例: `tests/test_webtransport_h3_close_session_message.py`
  の 0x2843) は対象外とする
- ローカルの `_encode_wt_*_capsule` ヘルパの複数ファイルへの重複自体は本 issue の
  対象外とし、ヘルパ内の種別バイト列の定数化のみ行う (ヘルパの統合は別途の
  論点。`_encode_rst_stream_frame` の統合は issue 0248 で追跡済み)
- 挙動は変えない。生成されるバイト列が同一であることを、既存テストの通過で
  確認する

## 完了条件

- h2 テストで使うカプセル種別の数値の定義が `tests/conftest.py` の 1 箇所に
  なっている (テストファイルのローカル `_WT_*` 定義が残っていない)
- 生リテラルでカプセル種別の値を組んでいる箇所が h2 テストに無くなっている
  (数値リテラル直書きと `bytes([0x99, ...])` / `b"\x68\x43"` の手組み Type
  バイト列を含む)
- 生成されるバイト列が変わっていない (全テストが通過する)
