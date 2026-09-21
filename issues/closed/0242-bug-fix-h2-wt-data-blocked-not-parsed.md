# h2 で WT_DATA_BLOCKED カプセルを読み出しも検証もしていない

- Created: 2026-09-20
- Completed: 2026-09-21
- Branch: feature/fix-h2-wt-data-blocked-not-parsed
- Polished: 2026-09-20

## 目的

draft-15 Section 6.8 は WT_DATA_BLOCKED (type=0x190B4D41) のペイロードに Maximum Data (可変長整数) を定めるが、`src/bindings/webtransport_h2.cpp` の `H2Session::process_capsule` は `CapsuleType::WtDataBlocked` を PADDING と同じ no-op 分岐にまとめており、フィールドの読み出しも検証も行わない。RFC 9297 Section 3.3 の「カプセルのペイロードはその定義が挙げるフィールドを正確に含まなければならない (MUST)」に反し、フィールドが欠けたカプセルや不正な可変長整数を持つカプセルが無言で読み捨てられる。送信側 (フロー制御の超過通知) は実装済みであるため、受信側だけが仕様の MUST を満たしていない状態になっている。

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
- 読み出し後に、消費バイト数が `length` と一致することを検証する (0241 と同じ規則。0241 が先に実装されて共通ヘルパが導入された場合は、`H2Session::read_capsule_varint` を直接使わずそのヘルパに合わせて二重検証にしない。0241 → 0242 の順で実装するのが自然である)。一致しない場合は余分なバイトを持つカプセルとして `H2Session::reset_stream_for_malformed_capsule` を呼ぶ
- 受信した値で状態を更新しない。draft-15 Section 6.8 は WT_DATA_BLOCKED を「フロー制御アルゴリズムの調整とデバッグの入力」と位置付ける advisory な通知であり、受信側 (エンドポイント) の MUST は無い (同節の MUST は中間装置向けの「consume して自身の制限に対する flow control signals を生成する」であり、本実装は中間装置ではない)。`H2Session::handle_wt_streams_blocked` と同様に、状態を更新せずイベントも push せずに受理する
- 値域の検証は行わない。Maximum Data はセッション全体の上限の申告であり、draft に値域の MUST が無い (`WT_STREAM_DATA_BLOCKED` も値域を検証していない)。検証対象はペイロードの形だけとする
- アプリへイベントを push しない。既存の `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` も push していない
- `case CapsuleType::WtDataBlocked:` を `case CapsuleType::Padding:` から分離し、新しいハンドラへ振り分ける。no-op のまま残るのは PADDING だけになるため、分岐のコメントも実態に合わせて更新する
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h`、`tests/test_webtransport_h2_flow_control_capsule.py` (受信側カプセルの注入テスト。既存の `_inject_capsule` と `_WT_MAX_DATA` と同じ形で WT_DATA_BLOCKED の定数と注入ヘルパを足す。カプセル種別の定数の集約は 0239 が扱うため、本 issue では既存の慣行どおり局所定数でよい)。実装時に次の 3 ファイルも対象になった
  - `tests/test_webtransport_h2_incomplete_capsule_payload.py` と `tests/test_webtransport_h2_capsule_trailing_bytes.py`: ペイロードの形の検証は、可変長整数を読むハンドラと固定フィールドのハンドラをカプセル横断で網羅するこの 2 ファイルが担う。読み出しを実装すると「9 ハンドラすべて」「8 ハンドラ」という網羅の主張が偽になるため、WT_DATA_BLOCKED の行を追加する
  - `tests/test_webtransport_h2_flow_control_replenishment.py`: 送信側が組んだ WT_DATA_BLOCKED を自実装の受信側に通し、送信形と受信側の形検証が一致していることを固定する

## 完了条件

- 正しい WT_DATA_BLOCKED (1 / 2 / 4 / 8 バイトの可変長整数) が受理され、セッションが存続する (対照)
- 空ペイロードの WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- 不完全な可変長整数を持つ WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- 余分なバイトを持つ WT_DATA_BLOCKED で PROTOCOL_ERROR の RST_STREAM が送出される
- malformed と判定したカプセルでは Error イベントを push せず、WT_CLOSE_SESSION も送出しない (RST_STREAM の submit が失敗した場合は 0237 と同じく Error イベントで観測可能になる。通常は submit が成功する)。0237 と同じく RST_STREAM の送出に伴うストリーム終了で `SessionClosed` が 1 回だけ通知され、その error_code は HTTP/2 の PROTOCOL_ERROR になる。0237 の `_assert_session_closed_by_protocol_error` と同じく、RST_STREAM の存在・Error イベント不在・`SessionClosed` (error_code は PROTOCOL_ERROR) を 1 つのヘルパでまとめて表明する (RED は RST_STREAM の表明の失敗として観測する)
- WT_DATA_BLOCKED は正常時もセッションのフロー制御状態を更新しない (Maximum Data の申告は advisory であり、`H2Session::handle_wt_max_data` のように `received_max_data` / `max_data_local` を書き換えない)。受理してもフロー制御状態が変わらないことを、観測可能な範囲で対照テストに固定する (`get_send_credit` が変化しないこと、セッションが存続しイベントが push されないこと)。フィールド単位の内部状態を観測するためのテスト専用 API の追加は求めない。HTTP/2 層の受信ウィンドウ会計 (`H2Session::consume_recv_bytes`) は全カプセルで動くため、この「状態不変」の対象外である
- 空ペイロード / 不完全な可変長整数 / 余分なバイトの 3 テストは、新しいハンドラの読み出しと長さの一致検証を外すと失敗することを実測で確認する (RED)。正常なカプセルの対照テストは現行実装でも通るため RED の対象外とする
- 全テストが通過する

## 対象外

- WT_DATA_BLOCKED をアプリへ通知するイベントの追加 (既存の BLOCKED 系カプセルも通知していない)
- 送信側の WT_DATA_BLOCKED 送出条件 (0156 で実装済み)
- `H2Session::handle_wt_streams_blocked` / `H2Session::handle_wt_max_streams` の既存の検証 (本 issue では変更しない)
- EndStream (CONNECT ストリームの終端) と同時に切り詰められたカプセルの扱い (0244 が扱う)

## 解決方法

- `src/bindings/webtransport_h2.cpp` に `H2Session::handle_wt_data_blocked` を追加し、`H2Session::read_capsule_varint` で Maximum Data を読んだあと、0241 の `H2Session::verify_capsule_payload_fully_read` で消費バイト数が `length` に達していることを検証する。フィールドの不足 (空ペイロード・不完全な可変長整数) と余分なバイトはいずれも `H2Session::reset_stream_for_malformed_capsule` (NGHTTP2_PROTOCOL_ERROR の RST_STREAM) になる。0241 のヘルパを使うため、同じ検証を二重に持たない
- `case CapsuleType::WtDataBlocked:` を `case CapsuleType::Padding:` から分離して新しいハンドラへ振り分けた。no-op で残るのは PADDING だけになったため、分岐のコメントも PADDING の根拠 (draft-15 Section 6.1 の "has no semantic value" と受信側に検証を義務付けていないこと) に合わせて更新した
- 受信した値では何もしない。draft-15 Section 6.8 のエンドポイント側に MUST は無く、同節の MUST は中間装置向けであること、エンドポイント側の関連する MUST は Section 4.4 の "An endpoint MUST NOT wait for a WT_DATA_BLOCKED ... capsule before sending a WT_MAX_DATA ... capsule" であることをコメントに残した。状態・フロー制御クレジットは更新せず、アプリへイベントも push しない
- 値域の検証は行わない。2^60 の MUST は Section 6.7 / 6.10 の Maximum Streams 系に固有で、Section 6.8 の Maximum Data には上限の MUST が無い (同じ block 系の `H2Session::handle_wt_stream_data_blocked` も検証していない)
- テストを追加した
  - `tests/test_webtransport_h2_incomplete_capsule_payload.py`: 不完全な可変長整数 (2 / 4 / 8 バイトのプレフィックスのみ) と空ペイロードの 4 行。モジュール docstring の網羅の主張を「9 ハンドラ」に更新した
  - `tests/test_webtransport_h2_capsule_trailing_bytes.py`: 余分なバイトを持つ違反行と、余分なバイトを持たない対照行。既定値未満の Maximum Data を使い、誤って `H2Session::handle_wt_max_data` に流れた場合も対照行で検出できるようにした。モジュール docstring を「8 ハンドラ」に更新した
  - `tests/test_webtransport_h2_flow_control_capsule.py`: 1 / 2 / 4 / 8 バイトの可変長整数と非最小符号化 (値 0 を 8 バイト。RFC 9297 Section 1.1) の受理、および受理後に送信クレジット・前回受信値 (`received_max_data` の汚染は後続の `WT_MAX_DATA` の減少判定で検出)・イベントが変化しないことを表明する
  - `tests/test_webtransport_h2_flow_control_replenishment.py`: 送信側が組んだ WT_DATA_BLOCKED を自実装の受信側に通し、送信形と受信側の形検証が一致していることを固定した
- RED は 4 通りで実測した: 分岐を no-op に戻すと 5 件 (不完全 4 件と余分なバイト 1 件)、長さの一致検証を外すと 1 件 (余分なバイト)、`H2Session::handle_wt_max_data` へ誤配線すると 6 件 (受理 2 件・非最小符号化・状態不変・trailing の対照行・往復)、新ハンドラを常に拒否すると往復テストが失敗する
- 全テストが通過する (1301 passed / 1 skipped)
