# h2 のテストでカプセル種別の定数が複数ファイルに重複定義されているのを集約する

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-h2-capsule-type-constants
- Polished: {YYYY-MM-DD}

## 目的

`tests/conftest.py` にはカプセルを組み立てるヘルパ (`_encode_wt_max_stream_data_capsule` / `_encode_wt_stream_data_blocked_capsule`) があるが、カプセル種別の数値そのものは各テストファイルが自前の `_WT_*` 定数や生リテラルで持っている。同じ数値が 3 から 4 箇所に散っており、カプセル種別を追加・変更したときの追従漏れと、レビュー時の突き合わせコストが生じる。

## 現状

- `WT_MAX_STREAM_DATA` (0x190B4D3E) が `tests/test_webtransport_h2_flow_control_capsule.py` / `tests/test_webtransport_h2_flow_control_replenishment.py` / `tests/test_webtransport_h2_initial_flow_control_fallback.py` / `tests/test_webtransport_h2_initiator_validation.py` の 4 ファイルで `_WT_MAX_STREAM_DATA` として定義され、`tests/test_webtransport_h2_close_session.py` は生リテラルで組んでいる
- `WT_STREAM_DATA_BLOCKED` (0x190B4D42) が `tests/test_webtransport_h2_flow_control_capsule.py` / `tests/test_webtransport_h2_flow_control_replenishment.py` で定義され、`tests/test_webtransport_h2_close_session.py` は生リテラルで組んでいる
- `tests/conftest.py` のエンコーダ 2 つは型番号を内部に持つため、定数としても使える形になっていない
- 同じカプセルを「conftest のヘルパで組む」「自前の定数 + `_encode_capsule` で組む」「生リテラルで組む」の 3 通りが併存している。直近の追加でも、あるファイルはヘルパ、別のファイルは自前定数という使い分けが生じた

## 設計方針

- カプセル種別の数値の出典を 1 箇所にする。第一候補は `tests/conftest.py` に `_WT_*` 定数を置き、各テストファイルはそれを import する形
- あわせて、同じカプセルを組む手段を 1 つに寄せる。conftest のエンコーダを使うか、定数 + `_encode_capsule` を使うかを統一し、生リテラルを残さない
- テストの意図を読みにくくしないこと。`tests/test_webtransport_h2_flow_control_capsule.py` は非準拠値の注入を目的に `_inject_capsule(capsule_type, payload)` の形を取っており、この形を維持するなら定数の import だけで済む
- 挙動は変えない。生成されるバイト列が同一であることを、既存テストの通過で確認する

## 完了条件

- カプセル種別の数値の定義が 1 箇所になっている
- 生リテラルでカプセルを組んでいる箇所が無くなっている
- 生成されるバイト列が変わっていない (全テストが通過する)
