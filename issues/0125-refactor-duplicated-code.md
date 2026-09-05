# 重複コードを共通化する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-duplicated-code
- Polished: 2026-09-05

## 目的

同一実装のコードが複数箇所に重複しており、仕様変更やバグ修正の影響範囲が広がっている。共通ヘルパーへ集約して保守性を高める。

## 現状

C++ 側:
- **URL パースの重複**: `src/bindings/webtransport_h2.cpp` の `H2Session::connect` と `src/bindings/webtransport_h3.cpp` の `H3Session::connect` にほぼ同一の簡易 URL パースがある (復帰値の差異のみ)
- **nghttp2_nv / nghttp3_nv 変換の重複**: `src/bindings/http2.cpp` に 4 箇所、`src/bindings/http3.cpp` に 4 箇所の同一変換コードがある (`std::vector<nghttp2_nv> nva` / `std::vector<nghttp3_nv> nva` の構築)。`src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h3.cpp` にも同種の nv 構築が各 3 件あるが、初期化形式が異なるため本 issue の対象外とする
- **セッション終了ガードの重複**: `src/bindings/webtransport_h3.cpp` に同一の 3 条件 (`session_ids_` / `pending_pre_accept_fin_session_ids_` / `pre_accept_fin_accepted_session_ids_` の確認) が 4 箇所ある (キャスト有無等の表記揺れはあるが判定は同一)
- **ストリーム種別判定の重複**: `src/bindings/webtransport_h3.cpp` の bind_control_stream / bind_qpack_encoder_stream / bind_qpack_decoder_stream に varint 上限チェックと単方向ストリーム判定が 3 重複

Python 側:
- `src/webtransport/h2/client.py` と `src/webtransport/h3/client.py` に同一の `_parse_url`
- `src/webtransport/quic/client.py` / `quic/server.py` / `http3/client.py` / `http3/server.py` / `h3/client.py` / `h3/server.py` に同一の `_normalize_addr` が 6 箇所
- `src/webtransport/quic/client.py` / `http3/client.py` / `h3/client.py` に同一の `_destination_for_packet` が 3 箇所。server 側の `_send_to` 等にも同型の送信先フォールバックがインラインで存在するが、フォールバック先が client (`_host` / `_port`) と server (`addr`) で異なるため本 issue の対象外とする
- 制御ストリーム setup が `http3/client.py` / `http3/server.py` / `h3/client.py` / `h3/server.py` に 4 箇所。レシーバ・引数・多重呼び出しガード (`_control_stream_id` / `streams_setup` 等) が異なるため、共通化時はインターフェースの確定が必要

対象外:
- QUIC varint デコード (`src/bindings/quic.cpp` の `decode_varint` と `src/bindings/webtransport_h2.cpp` の `H2Session::decode_varint`) はアルゴリズムは類似するがエラー契約が異なる (0 返却と `consumed` 設定 / `std::nullopt` 返却) ため、本 issue の対象外とする。必要なら別 issue 化する
- `tests/conftest.py` の証明書生成 (`test_certificates` fixture と `create_test_certificates`) は subject 属性・SAN 付与・ライフサイクルが異なるため、同一実装として扱わず本 issue の対象外とする。必要なら別 issue 化する
- テストヘルパーのワイヤ組み立て重複 (issue 0078 / 0081 でトラッキング済み) は本 issue の対象外

## 設計方針

- 共通ヘルパーに集約する。Python 側は `src/webtransport/_common.py` の新設を第一選択とし、C++ 側は複数翻訳単位で共有できる共通ヘッダー (`inline` ヘルパー等) に集約する。C++ の匿名名前空間は翻訳単位内での共有に留まるため、複数 `.cpp` 間の共有手段にはしない
- `nghttp2_nv` と `nghttp3_nv` は別型のため、型ごとにヘルパーを分ける
- 制御ストリーム setup の共通化時は、レシーバ・引数・多重呼び出しガードの扱いを確定させてから共通シグネチャを決める
- 挙動は変更しない純粋なリファクタリングとする (CHANGES.md の `### misc` に `[UPDATE]` で記載する)
- `_parse_url` の集約は pending の 0044 と競合するため、0044 が先行した場合は 0044 の `parse_wt_url` に寄せて本 issue では触らない。0125 が先行した場合は 0044 側で rebase する。いずれの場合も関数名は `parse_wt_url` に統一する

## 完了条件

- `src/bindings/http2.cpp` の 4 箇所と `src/bindings/http3.cpp` の 4 箇所の nv 変換が各型のヘルパーに集約され、重複定義が残らない
- `src/bindings/webtransport_h3.cpp` のセッション終了ガード 4 箇所がヘルパーに集約される
- `src/bindings/webtransport_h3.cpp` のストリーム種別判定 3 重複がヘルパーに集約される
- `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h3.cpp` の URL パース重複が集約される
- Python 側の `_normalize_addr` 6 箇所と `_destination_for_packet` 3 箇所が `_common.py` に集約される。`_parse_url` は依存関係の順序に従い、実施分のみ集約される
- 制御ストリーム setup 4 箇所が確定した共通シグネチャで集約される
- 全テストが通る

## 依存関係

- pending の 0044 と `_parse_url` / `_common.py` が競合する。0044 先行時は本 issue の `_parse_url` を対象外とし、0125 先行時は 0044 側で rebase する
