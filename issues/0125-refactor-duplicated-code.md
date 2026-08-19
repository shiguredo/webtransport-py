# 重複コードを共通化する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-duplicated-code
- Polished: {YYYY-MM-DD}

## 目的

同一実装のコードが複数箇所に重複しており、仕様変更やバグ修正の影響範囲が広がっている。共通ヘルパーへ集約して保守性を高める。

## 現状

C++ 側:
- **URL パースの重複**: `src/bindings/webtransport_h2.cpp` の `H2Session::connect` と `src/bindings/webtransport_h3.cpp` の `H3Session::connect` にほぼ同一の簡易 URL パースがある
- **nghttp2_nv / nghttp3_nv 変換の重複**: `src/bindings/http2.cpp` に 4 箇所、`src/bindings/http3.cpp` に 4 箇所の同一変換コードがある
- **セッション終了ガードの重複**: `src/bindings/webtransport_h3.cpp` に同一の 3 条件 (`session_ids_` / `pending_pre_accept_fin_session_ids_` / `pre_accept_fin_accepted_session_ids_` の確認) が 4 箇所ある
- **ストリーム種別判定の重複**: `src/bindings/webtransport_h3.cpp` の bind_control_stream / bind_qpack_encoder_stream / bind_qpack_decoder_stream に varint 上限チェックと単方向ストリーム判定が 3 重複
- **varint デコードの重複**: `src/bindings/quic.cpp` と `src/bindings/webtransport_h2.cpp` に同一の QUIC varint デコードがある

Python 側:
- `src/webtransport/h2/client.py` と `src/webtransport/h3/client.py` に同一の `_parse_url`
- `src/webtransport/quic/client.py` / `quic/server.py` / `http3/client.py` / `http3/server.py` / `h3/client.py` / `h3/server.py` に同一の `_normalize_addr` が 6 箇所
- `src/webtransport/quic/client.py` / `http3/client.py` / `h3/client.py` に同一の `_destination_for_packet` が 3 箇所
- 制御ストリーム setup が `http3/client.py` / `http3/server.py` / `h3/client.py` / `h3/server.py` に 4 箇所

テスト側:
- `tests/conftest.py` に同一の証明書生成が 2 実装 (fixture と create_test_certificates)

## 設計方針

- 共通ヘルパー (例: `src/webtransport/_common.py`、C++ の匿名名前空間) に集約する
- 挙動は変更しない純粋なリファクタリングとする (CHANGES.md には [UPDATE] で記載)
- テストヘルパーのワイヤ組み立て重複 (issue 0078 / 0081 でトラッキング済み) は本 issue の対象外

## 完了条件

- 上記の重複が共通化され、全テストが通る
