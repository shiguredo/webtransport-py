# WebTransport over HTTP/2 の初期フロー制御 SETTINGS が uint64 値を uint32 に切り詰めて WebTransport-Init と広告値が食い違う

- Created: 2026-09-07
- Completed: 2026-09-12
- Branch: feature/fix-h2-settings-uint64-truncation
- Polished: 2026-09-09

## 目的

`H2Session::initialize` は SETTINGS で `SETTINGS_WT_INITIAL_MAX_DATA` 等を送出する際に `static_cast<uint32_t>(config_.wt_initial_max_data)` を使う。HTTP/2 SETTINGS 値 (`nghttp2_settings_entry` の値は `uint32_t`) のため 32 ビットに収める必要があるが、`config_.wt_initial_max_data` は uint64 で 2^32 以上を設定すると黙って下位 32 ビットになる (実験: `wt_initial_max_stream_data = 2^32 + 5` を設定 → SETTINGS 値は 5、WebTransport-Init は 4294967301 で不一致。Init に載るのは stream_data 系のみのため stream_data で設定する)。受信側は大きい方を採用する実装 (stream_data は `record_received_limit`、max_data は `handle_wt_max_data`、max_streams は `handle_wt_max_streams`) のため機能的には吸収されるが、黙った切り詰めは診断不能のため生成時に拒否する。上限検査で `ValueError` にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::initialize` の `nghttp2_settings_entry` 配列で `SETTINGS_WT_INITIAL_MAX_DATA` / `SETTINGS_WT_INITIAL_MAX_STREAM_DATA_UNI` / `_BIDI_LOCAL` / `_BIDI_REMOTE` / `SETTINGS_WT_INITIAL_MAX_STREAMS_UNI` / `_BIDI` の 6 箇所で `static_cast<uint32_t>` 切り詰め
- `H2SessionConfig::wt_initial_max_data` / `wt_initial_max_stream_data` / `wt_initial_max_streams_bidi` / `wt_initial_max_streams_uni` は `uint64_t`
- `H2Session::encode_webtransport_init` は stream_data 系のみを 64 ビット値のまま `u=... , bl=... , br=...` で送出する
- 実験手順: `wt_initial_max_stream_data = 2^32 + 5` を設定した Config でセッションを生成し、送出 SETTINGS (値は 5) と WebTransport-Init 文字列 (値は 4294967301) を比較する。実行記録は未取得のため、実装時に再測定する
- `H2Session::encode_varint` (`webtransport_h2.cpp`) にも 2^62 上限検査は無い (issue 0175 の範囲であり、本 issue では触らない)
- 受信側の `handle_wt_max_streams` 系は Maximum Streams の 2^60 超を WT_FLOW_CONTROL_ERROR で拒否する MUST 実装済み (DATA 系に 2^60 規定はなく、送信側と非対称なのは仕様どおり)

## 設計方針

- `H2SessionConfig` の SETTINGS 送出対象 4 フィールド (`wt_initial_max_data` / `wt_initial_max_stream_data` / `wt_initial_max_streams_bidi` / `wt_initial_max_streams_uni`) に 2^32 - 1 上限検査を導入する。`H2Session::initialize` 内で超過時に `std::invalid_argument` を投げる (create の `nullptr` 経路を経由せず直接伝播し、nanobind の既定翻訳で `ValueError` になる)。Config 分離案は採らない (greater 採用で受信側は吸収でき、生成時拒否で診断可能性が足りるため API 増設は過剰)
- Config 型自体の見直し (uint64 → uint32 化等) は行わない。SETTINGS の uint32 制約は生成時の値検査で扱い、型は uint64 のまま維持する。本検査により 4 フィールドの初期値は 2^32 - 1 以下に制限され、初期広告として送出する SETTINGS と初期 `WT_MAX_DATA` / `WT_MAX_STREAMS` カプセル / WebTransport-Init の値も同じ上限になる。一方、以後の補充カプセル (`WT_MAX_DATA` / `WT_MAX_STREAM_DATA` / `WT_MAX_STREAMS`) は受信量やストリーム開設に応じて 2^32 - 1 を超え得る (上限は `kMaxVarint` / `kMaxStreamsLimit`) ため、型は uint64 のまま維持する
- issue 0175 (encode_varint 上限検査) とは別 PR で実装する (varint 検査と SETTINGS 上限は直交し、相互前提を持たない)。0175 側の統合言及と矛盾しない

## 完了条件

- `wt_initial_max_stream_data = 2^32 + 5` を設定するとセッション作成時に `ValueError` になること
- 32 bit 範囲内の値は従来どおり動作すること
- `tests/prop_webtransport_h2.py` の Config setter の property test 4 件は setter が uint64 全域を受け付ける現状のまま維持し、`create_client` / `create_server` で 2^32 - 1 は成功・2^32 は `ValueError` になる境界テストを `tests/test_webtransport_h2_settings_limit.py` (新規) に追加すること
- 既存のテスト全 976 件が引き続き通過すること

## 解決方法

- `H2Session::initialize` の冒頭で `H2SessionConfig` の SETTINGS 送出対象 4 フィールド (`wt_initial_max_data` / `wt_initial_max_stream_data` / `wt_initial_max_streams_bidi` / `wt_initial_max_streams_uni`) を `kMaxSettingsValue` (2^32 - 1、RFC 9113 Section 6.5.1) と比較し、超過は `std::invalid_argument` (nanobind の既定翻訳で `ValueError`) にした
- これにより SETTINGS の `static_cast<uint32_t>` による黙った切り詰めが発生せず、SETTINGS と WebTransport-Init / 初期 `WT_MAX_DATA` / `WT_MAX_STREAMS` カプセルの値が一致する
- `2^32 - 1` の上限は draft-15 Section 6.7 / 6.10 の Maximum Streams 2^60 制限と、0175 で追加した varint (2^62 - 1) の防御検査を同時に満たす (旧検査 2^62 / 2^60 は本検査に置き換えた)
- `tests/test_webtransport_h2_settings_limit.py` を新規作成し、4 フィールド × client / server で 2^32 - 1 の生成成功と 2^32 (再現値の 2^32 + 5 を含む) の `ValueError` を検証した
- `tests/prop_webtransport_h2.py` の Config 上限 property test の対象を 4 フィールド・2^32 以上に更新し、`tests/test_webtransport_h2_reset_validation.py` の Config 境界テストは本ファイルへ移動した
- `src/bindings/webtransport_h2.h` の Config コメント、`skills/webtransport-py/SKILL.md`、`src/webtransport/h2/client.py` の docstring を 2^32 - 1 上限に更新した
- `CHANGES.md` の develop の FIX エントリを最終的な挙動 (2^32 以上を生成時 `ValueError`) に更新した
- 全 1016 テストが通過することを確認した
