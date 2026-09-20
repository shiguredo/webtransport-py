# h2 で WT_DATA_BLOCKED カプセルを読み出しも検証もしていない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-wt-data-blocked-not-parsed
- Polished: {YYYY-MM-DD}

## 目的

draft-15 Section 6.8 は WT_DATA_BLOCKED (type=0x190B4D41) のペイロードに Maximum Data (可変長整数) を定めるが、`src/bindings/webtransport_h2.cpp` の `H2Session::process_capsule` は `CapsuleType::WtDataBlocked` を PADDING と同じ no-op 分岐にまとめており、フィールドの読み出しも検証も行わない。RFC 9297 Section 3.3 の「カプセルのペイロードはその定義が挙げるフィールドを正確に含まなければならない (MUST)」に反し、フィールドが欠けたカプセルや不正な可変長整数を持つカプセルが無言で受理される。送信側 (フロー制御の超過通知) は実装済みであるため、受信側だけが仕様の MUST を満たしていない状態になっている。

## 現状

- `H2Session::process_capsule` の `case CapsuleType::WtDataBlocked:` は `case CapsuleType::Padding:` と同じ `break;` にまとめられ、「フロー制御通知・PADDING は現時点では状態更新のみ不要」というコメントが付いている。ペイロードは `H2Session::process_capsule` の引数として渡されているが参照されない
- 送信側は `H2Session::send_stream_data` のフロー制御超過の経路で `CapsuleType::WtDataBlocked` を送出している (0156 で実装済み)。同じカプセルを受信側だけが処理しない
- 兄弟カプセルには受信側の実装がある
  - `H2Session::handle_wt_stream_data_blocked` は Stream ID と Maximum Stream Data を読み、方向 (`is_receivable_data_capsule`)・終端状態・解放済みを検証したうえで、advisory な通知として状態を更新せずに受理する
  - `H2Session::handle_wt_streams_blocked` は Maximum Streams を読み、2^60 超を WT_FLOW_CONTROL_ERROR として扱う
  - `H2Session::handle_wt_max_data` は Maximum Data を読み、減少値を WT_FLOW_CONTROL_ERROR として扱う
- 実測した結果: 次の 3 通りはいずれも RST_STREAM を送出せず、Error イベントも push せず、セッションが存続する
  - 空ペイロード (Length 0。Maximum Data が無い)
  - 4 バイト可変長整数の先頭 1 バイト (`0x80`) だけのペイロード (Maximum Data が不完全)
  - Maximum Data の後に `0x00` を 1 バイト加えたペイロード
  - 期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)

## 設計方針

- `H2Session::handle_wt_data_blocked(int32_t session_id, const uint8_t* payload, size_t length)` を追加し、`H2Session::read_capsule_varint` で Maximum Data を読む。不完全な可変長整数は 0237 の経路 (PROTOCOL_ERROR の RST_STREAM) になる
- 読み出し後に、消費バイト数が `length` と一致することを検証する (0241 と同じ規則)。一致しない場合は余分なバイトを持つカプセルとして `H2Session::reset_stream_for_malformed_capsule` を呼ぶ
- 受信した値で状態を更新しない。draft-15 Section 6.8 は WT_DATA_BLOCKED を「フロー制御アルゴリズムの調整とデバッグの入力」と位置付ける advisory な通知であり、受信側の MUST は無い。`H2Session::handle_wt_streams_blocked` が同種の通知を状態更新せずに受理しているのと同じ扱いにする
- 値域の検証は行わない。Maximum Data はセッション全体の上限の申告であり、draft に値域の MUST が無い (`WT_STREAM_DATA_BLOCKED` も値域を検証していない)。検証対象はペイロードの形だけとする
- アプリへイベントを push しない。既存の `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` も push していない
- `case CapsuleType::WtDataBlocked:` を `case CapsuleType::Padding:` から分離し、新しいハンドラへ振り分ける。no-op のまま残るのは PADDING だけになるため、分岐のコメントも実態に合わせて更新する
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h`、`tests/`

## 完了条件

- 正しい WT_DATA_BLOCKED (1 / 2 / 4 / 8 バイトの可変長整数) が受理され、セッションが存続する (対照)
- 空ペイロードの WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- 不完全な可変長整数を持つ WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- 余分なバイトを持つ WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- malformed と判定したカプセルがセッションのフロー制御状態を更新しないこと (該当する状態が変わらないこと) を検証する
- 各テストは新しいハンドラの検証を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- WT_DATA_BLOCKED をアプリへ通知するイベントの追加 (既存の BLOCKED 系カプセルも通知していない)
- 送信側の WT_DATA_BLOCKED 送出条件 (0156 で実装済み)
- `H2Session::handle_wt_streams_blocked` / `H2Session::handle_wt_max_streams` の既存の検証 (本 issue では変更しない)
