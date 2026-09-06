# WebTransport over HTTP/2 の初期フロー制御 SETTINGS が uint64 値を uint32 に切り詰めて WebTransport-Init と広告値が食い違う

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-settings-uint64-truncation
- Polished: {YYYY-MM-DD}

## 目的

`H2Session::initialize` は SETTINGS で `SETTINGS_WT_INITIAL_MAX_DATA` 等を送出する際に `static_cast<uint32_t>(config_.wt_initial_max_data)` を使う。HTTP/2 SETTINGS 値は 32 ビットなので必然だが、`config_.wt_initial_max_data` は uint64 で 2^32 以上を設定すると黙って下位 32 ビットになる (実験: `2^32+5 → 5`)。WebTransport-Init ヘッダー (`encode_webtransport_init`) は 64 ビット値のまま送るため、SETTINGS と header で矛盾した広告になる。仕様上どちらを優先するかも不明。上限検査で `ValueError` にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::initialize` の `nghttp2_settings_entry` 配列で `SETTINGS_WT_INITIAL_MAX_DATA` / `SETTINGS_WT_INITIAL_MAX_STREAM_DATA_UNI` / `_BIDI_LOCAL` / `_BIDI_REMOTE` / `SETTINGS_WT_INITIAL_MAX_STREAMS_UNI` / `_BIDI` の 6 箇所で `static_cast<uint32_t>` 切り詰め
- `H2SessionConfig::wt_initial_max_data` / `wt_initial_max_stream_data` / `wt_initial_max_streams_bidi` / `wt_initial_max_streams_uni` は `uint64_t`
- `H2Session::encode_webtransport_init` は 64 ビット値のまま `u=... , bl=... , br=...` を送出
- 実験: `wt_initial_max_data = 2^32 + 5` を設定 → SETTINGS 値は 5、WebTransport-Init は 4294967301 で不一致
- `H2Session::encode_varint` (`webtransport_h2.cpp`) にも 2^62 上限検査は無い (issue 0175 の範囲)
- draft-ietf-webtrans-http2-15 Section 11.2 の SETTINGS 定義は 32 bit
- 受信側の `handle_wt_max_data` 等は 2^60 超を WT_FLOW_CONTROL_ERROR で拒否する MUST 実装済み (送信側と非対称)

## 設計方針

- `H2SessionConfig` の各 uint64 フィールドに 32 bit 上限検査を導入する。SETTINGS で送出する項目 (`wt_initial_max_data` / `wt_initial_max_stream_data` / `wt_initial_max_streams_*`) は 2^32 - 1 を超える値を `H2Session::create_client` / `create_server` の時点で拒否 (`std::invalid_argument`)
- または SETTINGS を uint32 で受ける新 Config フィールドと、WT_MAX_DATA カプセルを別 API で送るための uint64 Config フィールドを分ける
- CODEBASE.md の「破壊的変更を積極的にする」方針に沿い、Config 型の見直しを検討する
- 受信側の 2^60 上限検査 (現状の MUST 実装) との対称性を保つ
- issue 0175 (encode_varint 上限検査) と統合して実装する

## 完了条件

- `wt_initial_max_data = 2^32 + 5` を設定するとセッション作成時に `ValueError` になること
- 32 bit 範囲内の値は従来どおり動作すること
- SETTINGS と WebTransport-Init の広告値が一致すること
- `tests/prop_webtransport_h2.py` の Config 値の property test で 32 bit 上限を検証すること
- 既存のテスト全 822 件が引き続き通過すること
