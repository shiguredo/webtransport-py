# h2 で識別フィールドの後に余分なバイトがあるカプセルが検証されない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-capsule-trailing-bytes
- Polished: {YYYY-MM-DD}

## 目的

RFC 9297 Section 3.3 は「各カプセルのペイロードはその定義が挙げるフィールドを正確に含まなければならない。識別フィールドの後に余分なバイトを含むペイロード、または識別フィールドの終端より前に終わるペイロードは、malformed または incomplete なメッセージとして扱わなければならない (MUST)」と定める。`src/bindings/webtransport_h2.cpp` のカプセルハンドラは各フィールドを読んだ後に残りのバイトを検証しないため、末尾に余分なバイトを付けたカプセルがそのまま受理され、無言で読み捨てられる。プロトコル違反を検知できないままセッションが継続する。

## 現状

- 固定フィールドのみからなるカプセルのハンドラは、いずれも最後のフィールドを読んだ時点で消費バイト数の加算を止め、`length` との一致を検証しない
  - `H2Session::handle_wt_reset_stream` (Stream ID / Application Error Code / Reliable Size)
  - `H2Session::handle_wt_stop_sending` (Stream ID / Application Error Code)
  - `H2Session::handle_wt_max_data` (Maximum Data)
  - `H2Session::handle_wt_max_stream_data` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_max_streams` (Maximum Streams)
  - `H2Session::handle_wt_stream_data_blocked` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_streams_blocked` (Maximum Streams)
- 根拠となる記述: `H2Session::handle_wt_stop_sending` は `read_capsule_varint` が返す `error_code_len` を最後のフィールドで受け取ったきり以後使わない。`H2Session::handle_wt_max_stream_data` の `max_data_len`、`H2Session::handle_wt_streams_blocked` の `max_streams_len` も同じである。最後のフィールドまで読んだ位置が `length` に達したことを検証する箇所が無い
- `H2Session::process_capsule` は `CapsuleType::WtDrainSession` を `H2Session::handle_wt_drain_session(session_id)` へ渡すだけでペイロードを参照しない。draft-15 Section 6.13 は WT_DRAIN_SESSION の Length を 0 と定めるため、ペイロードを持つ WT_DRAIN_SESSION は余分なバイトを持つカプセルである
- 実測した結果 (いずれも RST_STREAM は送出されず、セッションが存続する)
  - Stream ID + Application Error Code に `0x00` を 1 バイト加えた WT_STOP_SENDING → `StopSending` イベントが通常どおり push される
  - Stream ID + Application Error Code + Reliable Size に `0x00` を 1 バイト加えた WT_RESET_STREAM → `StreamReset` イベントが通常どおり push される
  - Maximum Data に `0x00` を 1 バイト加えた WT_MAX_DATA → 状態更新 (前回受信値の記録) が行われる
  - `0x00` を 1 バイトのペイロードに持つ WT_DRAIN_SESSION → `SessionDraining` イベントが通常どおり push される
  - 期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)
- 副次的な影響として、`H2Session::handle_wt_max_data` などの値を使う検証は余分なバイトの有無に関わらず動作するため、受理された違反カプセルが状態を更新してしまう
- ペイロードが識別フィールドの途中で終わる場合 (不完全なペイロード) は 0237 で対応済みであり、本 issue は同じ MUST のもう一方を扱う

## 設計方針

- malformed の扱いは 0237 で導入した `H2Session::reset_stream_for_malformed_capsule` に揃える。`is_terminated` / `is_established` を落として以後のカプセル処理と送受信を止め、`NGHTTP2_PROTOCOL_ERROR` の RST_STREAM を送出する
- 各ハンドラで最後のフィールドを読んだ後の消費バイト数が `length` と一致することを検証し、一致しない場合は `reset_stream_for_malformed_capsule` を呼んで戻る。判定は共通ヘルパ (`H2Session::read_capsule_varint` と同じ位置付け) に置くか各ハンドラに置くかは実装時に決め、いずれの場合も「フィールドの形」の検証であることをコメントに書く
- 検証の順序は、ペイロードの形の検証をフィールドの意味論 (方向・ストリーム状態・値域) の検証より前に置く。0237 が `read_capsule_varint` のデコード失敗を意味論より前に置いたのと同じ順序とし、形が不正なカプセルを状態検証へ通さない
- `WT_DRAIN_SESSION` は `H2Session::handle_wt_drain_session` に `payload` と `length` を渡し、`length != 0` を malformed として扱う
- 余分なバイトを持たないカプセルの挙動は変えない。`read_capsule_varint` の入力契約 (`payload + offset` と `length - offset`) も変えない
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (新ヘルパを追加する場合)、`tests/`

## 完了条件

- 余分なバイトを持つ WT_RESET_STREAM / WT_STOP_SENDING / WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS / WT_STREAM_DATA_BLOCKED / WT_STREAMS_BLOCKED の受信で、PROTOCOL_ERROR の RST_STREAM が送出される
- ペイロードを持つ WT_DRAIN_SESSION の受信で、PROTOCOL_ERROR の RST_STREAM が送出される
- malformed と判定したカプセルが状態を更新しないこと (該当するイベントが push されないこと) を検証する
- 余分なバイトを持たない同じカプセルが従来どおり受理される (対照)
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- Application Error Code が 4 バイト未満の WT_CLOSE_SESSION の扱い (0243 で扱う)
- `WT_DATA_BLOCKED` の読み出しと検証 (0242 で扱う)
- ペイロードが識別フィールドの終端に達しない場合の検証 (0237 で対応済み)
- クリーンな END_STREAM の直前に切り詰められたカプセルの検証 (0244 で扱う)
- 未知の Capsule Type の読み捨て (RFC 9297 Section 3.2 のとおり現状維持)
