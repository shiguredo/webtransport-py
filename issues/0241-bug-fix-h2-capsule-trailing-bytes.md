# h2 で識別フィールドの後に余分なバイトがあるカプセルが検証されない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-capsule-trailing-bytes
- Polished: 2026-09-20

## 目的

RFC 9297 Section 3.3 は「各カプセルのペイロードはその定義が挙げるフィールドを正確に含まなければならない。識別フィールドの後に余分なバイトを含むペイロード、または識別フィールドの終端より前に終わるペイロードは、malformed または incomplete なメッセージとして扱わなければならない (MUST)」と定める。`src/bindings/webtransport_h2.cpp` のカプセルハンドラは各フィールドを読んだ後に残りのバイトを検証しないため、末尾に余分なバイトを付けたカプセルがそのまま受理され、無言で読み捨てられる。プロトコル違反を検知できないままセッションが継続する。

## 現状

- 固定フィールドのみからなるカプセルのハンドラは、いずれも最後のフィールドを読んだ時点で消費バイト数の加算を止め、`length` との一致を検証しない。`H2Session::read_capsule_varint` の `H2Session::decode_varint` は「先頭の可変長整数が完結するだけのバイト数があるか」しか見ないため、可変長整数 1 つだけのカプセル (WT_MAX_DATA / WT_MAX_STREAMS / WT_STREAMS_BLOCKED) でも末尾の余分なバイトは残ったまま読み捨てられる
  - `H2Session::handle_wt_reset_stream` (Stream ID / Application Error Code / Reliable Size)
  - `H2Session::handle_wt_stop_sending` (Stream ID / Application Error Code)
  - `H2Session::handle_wt_max_data` (Maximum Data)
  - `H2Session::handle_wt_max_stream_data` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_max_streams` (Maximum Streams)
  - `H2Session::handle_wt_stream_data_blocked` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_streams_blocked` (Maximum Streams)
- 根拠となる記述: `H2Session::handle_wt_stop_sending` は `read_capsule_varint` が返す `error_code_len` を最後のフィールドで受け取ったきり以後使わない。`H2Session::handle_wt_max_stream_data` の `max_data_len`、`H2Session::handle_wt_streams_blocked` の `max_streams_len` も同じである。`H2Session::handle_wt_max_data` / `handle_wt_max_streams` / `handle_wt_stream_data_blocked` は戻り値の消費バイト数ごと破棄している。7 ハンドラのいずれにも、最後のフィールドまで読んだ位置が `length` に達したことを検証する箇所が無い
- `H2Session::process_capsule` は `CapsuleType::WtDrainSession` を `H2Session::handle_wt_drain_session(session_id)` へ渡すだけでペイロードを参照しない。draft-15 Section 6.13 は WT_DRAIN_SESSION の Length を 0 と定めるため、ペイロードを持つ WT_DRAIN_SESSION は余分なバイトを持つカプセルである
- 実測した結果 (いずれも余分なバイトは検証されず RST_STREAM は送出されない)
  - Stream ID + Application Error Code に `0x00` を 1 バイト加えた WT_STOP_SENDING → `StopSending` イベントが通常どおり push され、セッションが存続する
  - Stream ID + Application Error Code + Reliable Size に `0x00` を 1 バイト加えた WT_RESET_STREAM → `StreamReset` イベントが通常どおり push され、セッションが存続する
  - Maximum Data を本実装の `Config` の既定値 (`wt_initial_max_data` = 1048576) 以上 (実測では 2,000,000) にした WT_MAX_DATA に `0x00` を 1 バイト加える → セッションが存続し、前回受信値の記録 (状態更新) が行われる。Maximum Data が受信済みの値より小さい場合 (例: 100) は、余分なバイトの有無に関わらず既存の `WT_FLOW_CONTROL_ERROR` 経路でセッションが閉じる。この既存の減少値検証と混同しないこと (1048576 は仕様の既定値ではなく本実装の設定値であり、テストでは設定値以上を使う)
  - `0x00` を 1 バイトのペイロードに持つ WT_DRAIN_SESSION → `SessionDraining` イベントが通常どおり push され、セッションが存続する
  - 期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)
- 副次的な影響として、`H2Session::handle_wt_max_data` などの値を使う検証は余分なバイトの有無に関わらず動作するため、受理された違反カプセルが状態を更新してしまう
- ペイロードが識別フィールドの途中で終わる場合 (不完全なペイロード) は 0237 で対応済みであり、本 issue は同じ MUST のもう一方を扱う

## 設計方針

- malformed の扱いは 0237 で導入した `H2Session::reset_stream_for_malformed_capsule` に揃える。`is_terminated` / `is_established` を落として以後のカプセル処理と送受信を止め、`NGHTTP2_PROTOCOL_ERROR` の RST_STREAM を送出する
- 7 ハンドラで、読み出したフィールドの合計バイト数が `length` と一致することを検証し、一致しない場合は `reset_stream_for_malformed_capsule` を呼んで戻る。`H2Session::handle_wt_max_data` / `handle_wt_max_streams` / `handle_wt_streams_blocked` は現在 `read_capsule_varint` の戻り値の消費バイト数を破棄しているため、この 3 つは読み出し位置 (消費バイト数) を保持する形に直す
- 判定は共通ヘルパ (`H2Session::read_capsule_varint` と同じ位置付け) に置くか各ハンドラに置くかは実装時に決めるが、7 ハンドラで同じ形にそろえる (ハンドラごとに判定の書き方を変えない)。`read_capsule_varint` のシグネチャを変えて `length` との一致検証まで担わせる場合は、0237 で導入したデコード失敗時の経路と矛盾しないようにする。いずれの場合も「フィールドの形」の検証であることをコメントに書く
- この規則を掛けるのは固定フィールドのみからなるカプセル (上の 7 ハンドラと WT_DRAIN_SESSION) に限る。残りのバイトが正当なペイロードであるカプセルには掛けない
  - `H2Session::handle_wt_stream` は Stream ID の後ろが Stream Data である
  - `H2Session::handle_datagram` はペイロード全体がデータグラムである
  - `H2Session::handle_wt_close_session` は Application Error Code の後ろが Application Error Message である
  - PADDING の Padding フィールドは任意長である (`H2Session::process_capsule` の no-op 分岐のまま)
- 検証の順序は、そのカプセルの全フィールドを読み終えた直後にペイロードの形を検証し、その後にフィールドの意味論 (方向・ストリーム状態・値域) を検証する。0237 が `read_capsule_varint` のデコード失敗を意味論より前に置いたのと同じ順序とし、形が不正なカプセルを状態検証へ通さない
- 例外として、`H2Session::handle_wt_reset_stream` の Error Code の範囲検証は現在の位置 (Reliable Size を読む前) を維持する。既存のコメントどおり「終端状態・ Reliable Size より先に検証する」という判断であり、この位置より後ろにある検証の順序は変えない。したがって「余分なバイトを持ち、かつ Error Code が 0xffffffff を超える WT_RESET_STREAM」では、既存の `WT_ERROR` セッションエラーが形の検証より優先される
- `WT_DRAIN_SESSION` は `H2Session::handle_wt_drain_session` に `payload` と `length` を渡し、`length != 0` を malformed として扱う
- 余分なバイトを持たないカプセルの挙動は変えない。各ハンドラの読み出し順序 (先頭から `payload + offset` / `length - offset` で読む形) は変えず、共通ヘルパのシグネチャを広げる場合も既存の呼び出しの意味を変えない
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (共通ヘルパを追加する場合)、`tests/test_webtransport_h2_capsule_trailing_bytes.py` (新規。0237 の `tests/test_webtransport_h2_incomplete_capsule_payload.py` と同じく Sans-I/O のセッションペアへワイヤ注入する構成にし、余分なバイトを持つカプセルと対照を検証する)

## 完了条件

- 余分なバイトを持つ WT_RESET_STREAM / WT_STOP_SENDING / WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS / WT_STREAM_DATA_BLOCKED / WT_STREAMS_BLOCKED の受信で、Error Code の範囲検証など既存の意味論検証が先に成立しない入力については PROTOCOL_ERROR の RST_STREAM が送出される
- ペイロードを持つ WT_DRAIN_SESSION の受信で、PROTOCOL_ERROR の RST_STREAM が送出される
- malformed と判定したカプセルが状態を更新しないこと (該当するイベントが push されないこと) を検証する
- 余分なバイトを持ち、かつ Error Code が 0xffffffff を超える WT_RESET_STREAM では、既存の意味論検証 (Error Code の範囲検証) が先に走り `WT_ERROR` セッションエラーになる (`kWtError` = 0x52 の Error イベントと WT_CLOSE_SESSION の送出。PROTOCOL_ERROR の RST_STREAM は送出しない)。この優先関係をテストで固定する。このテストは形の検証が無い現行実装でも同じ入力で通るため「追加した検証を外す」RED の対象外とし、代わりに Error Code の範囲検証を Reliable Size の読み出しより後ろへ移す (順序を入れ替える) と失敗することで RED を確認する
- 余分なバイトを持たない同じカプセルが従来どおり受理される (対照)
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED。ただし上の優先関係のテストは順序の入れ替えで RED を確認する)
- 全テストが通過する

## 対象外

- Application Error Code が 4 バイト未満の WT_CLOSE_SESSION の扱い (0243 で扱う)
- `WT_DATA_BLOCKED` の読み出しと検証 (0242 で扱う)
- ペイロードが識別フィールドの終端に達しない場合の検証 (0237 で対応済み)
- クリーンな END_STREAM の直前に切り詰められたカプセルの検証 (0244 で扱う)
- 未知の Capsule Type の読み捨て (RFC 9297 Section 3.2 のとおり現状維持)
