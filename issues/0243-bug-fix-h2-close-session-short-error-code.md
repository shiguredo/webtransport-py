# h2 で Application Error Code が 4 バイト未満の WT_CLOSE_SESSION を正常終了として受理する

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-close-session-short-error-code
- Polished: 2026-09-20

## 目的

draft-15 Section 6.12 の WT_CLOSE_SESSION は Application Error Code (32 ビット固定) と Application Error Message (残り全部) を持つ。RFC 9297 Section 3.3 は識別フィールドの終端より前にペイロードが終わるカプセルを malformed または incomplete なメッセージとして扱う MUST を定めるが、`src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` は Application Error Code が 4 バイト未満の場合にそれを検知せず、エラーコード 0 の正常終了としてセッションを閉じる。プロトコル違反のカプセルが正当な終了要求と同じ扱いになる。

## 現状

- `H2Session::handle_wt_close_session` は `if (length >= 4)` の内側でのみ Application Error Code を組み立て、`length < 4` では `error_code = 0` / `message_len = 0` のまま後段の検証 (1024 バイト超過・UTF-8) を通り、`SessionClosed` (error_code 0) を push してセッションを終了する
- したがって、ペイロード 0 / 1 / 2 / 3 バイトの WT_CLOSE_SESSION はいずれも「アプリケーションエラーコード 0 での正常終了」になる。draft-15 Section 6.12 の「WT_CLOSE_SESSION 無しのクリーンな終了は error code 0 と等価」と同じ観測結果になり、アプリからは違反を区別できない
- 0237 が扱ったのは可変長整数のデコード失敗である。WT_CLOSE_SESSION の Application Error Code は固定長 (4 バイト) であり `read_capsule_varint` を通らないため、0237 の経路では検知されない。固定長フィールドが欠ける経路は未対応のまま残っている
- 実測した結果: ペイロード `b"\x00\x00\x00"` (3 バイト) の WT_CLOSE_SESSION を受信すると、RST_STREAM は送出されず、`SessionClosed` (error_code 0) が push されてセッションが終了する。期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)
- h3 層 (WebTransport over HTTP/3) は同じ入力を不正として扱っている。`tests/test_webtransport_h3_close_session_message.py` の `test_client_receive_too_short_length_resets_with_h3_message_error` は、ペイロード 3 バイトの WT_CLOSE_SESSION で nghttp3 の LENGTH 検査が `NGHTTP3_ERR_H3_MESSAGE_ERROR` を返し、0x010E (H3_MESSAGE_ERROR) のリセットが発生することを固定している。h2 層だけが同じ入力を正常終了として受理しており、層間で挙動が食い違っている
- `length == 4` は Application Error Message が空の正しいカプセルであり、`length > 4` はメッセージ付きの正しいカプセルである。どちらも現状どおり受理される必要がある

## 設計方針

- `length < 4` を malformed として `H2Session::reset_stream_for_malformed_capsule` で扱う。0237 と同じ後始末 (`is_terminated` / `is_established` を落として `NGHTTP2_PROTOCOL_ERROR` の RST_STREAM を送出する) を使い、正常終了としての `SessionClosed` (error_code 0) を push しない。0237 と同じく `SessionClosed` は RST_STREAM の送出に伴うストリーム終了で 1 回だけ通知され、error_code は HTTP/2 の PROTOCOL_ERROR になる。応答の WT_CLOSE_SESSION も送出しない (0237 のテストと同じ観測)
- 検証は Application Error Message の検証 (1024 バイト超過・UTF-8) より前に置く。フィールドが欠けているペイロードのメッセージを検証しても意味が無いためである
- `length == 4` と `length > 4` の挙動は変えない
- 変更対象: `src/bindings/webtransport_h2.cpp`、`src/bindings/webtransport_h2.h` (`WtSessionInfo::is_terminated` のコメント更新のみ。挙動の変更は無い)、`tests/test_webtransport_h2_close_session_message.py`。テストの書き方についての補足:
  - 既存の `_encode_wt_close_session_capsule(error_code, message)` は Application Error Code を必ず 4 バイト載せるため、0 〜 3 バイトのペイロードは `_encode_capsule(0x2843, ...)` を直接使って組み立てる (`tests/test_webtransport_h3_close_session_message.py` の同種テストと同じ形)
  - 「応答の WT_CLOSE_SESSION が送出されない」側の表明には既存の `_assert_no_wt_error_sent` を使う (`_assert_wt_error_sent` は WT_ERROR の送出を前提とするため使わない)。`_assert_no_wt_error_sent` は内部で `server.send()` を呼ぶため、RST_STREAM の検出と `SessionClosed` の観測は同一の wire (`server.send()` の戻り値) に対して行い、`send()` を 2 回消費して表明が空虚にならないようにする
  - RST_STREAM フレームを組み立てるヘルパは `tests/test_webtransport_h2_incomplete_capsule_payload.py` にローカル実装があり (0237)、その集約は 0248 が追跡している。0248 の 変更対象は現時点で `tests/conftest.py` と既存 3 テストファイルであり本 issue のテストファイルを含まないため、本 issue を先に実装してローカルヘルパを増やす場合は 0248 の磨き上げ時に範囲へ追加する (0237 のローカルヘルパに倣ってよい)
- `src/bindings/webtransport_h2.h` の `WtSessionInfo::is_terminated` のコメントは「WT_CLOSE_SESSION 受信はエントリ削除で表現する」と限定なしで書かれている。本修正後は「正常な WT_CLOSE_SESSION 受信はエントリ削除、不正な短いカプセルは `is_terminated`」になるため、実態に合うようコメントを更新する (挙動の変更は伴わない)

## 完了条件

- ペイロード 0 / 1 / 2 / 3 バイトの WT_CLOSE_SESSION の受信で PROTOCOL_ERROR の RST_STREAM が送出される。正常終了としての `SessionClosed` (error_code 0) は push されず、RST_STREAM の送出に伴うストリーム終了で `SessionClosed` (error_code は HTTP/2 の PROTOCOL_ERROR) が 1 回だけ通知される。Error イベントも応答の WT_CLOSE_SESSION も送出されない
- ペイロード 4 バイト (Application Error Message が空) が従来どおり受理され、`SessionClosed` の error_code がペイロードの値になる (対照。error code には 0 以外の値を使う。0 では「ペイロードの値になる」の表明が空虚になる)
- ペイロード 5 バイト以上で Application Error Message が従来どおり `SessionClosed` に載る (対照)
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED。上の 2 つの対照テストは現行実装でも通るため RED の対象外とする)
- 全テストが通過する

## 対象外

- 識別フィールドの後に余分なバイトがあるカプセルの検証 (0241 で扱う)。ただし WT_CLOSE_SESSION は Application Error Message が残り全部を占めるため余分なバイトは生じず、0241 でも `H2Session::handle_wt_close_session` は対象外である (0241 の設計方針を参照)。本 issue もこの経路には手を入れない
- Application Error Message が 1024 バイトを超える場合と不正な UTF-8 の場合 (既存の WT_ERROR 経路を維持する)
- ピアが WT_CLOSE_SESSION を送らずに END_STREAM だけを送る場合の扱い (既存の `H2Session::handle_end_stream` を維持する)
