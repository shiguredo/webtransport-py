# h2 で Application Error Code が 4 バイト未満の WT_CLOSE_SESSION を正常終了として受理する

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-close-session-short-error-code
- Polished: {YYYY-MM-DD}

## 目的

draft-15 Section 6.12 の WT_CLOSE_SESSION は Application Error Code (32 ビット固定) と Application Error Message (残り全部) を持つ。RFC 9297 Section 3.3 は識別フィールドの終端より前にペイロードが終わるカプセルを malformed または incomplete なメッセージとして扱う MUST を定めるが、`src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` は Application Error Code が 4 バイト未満の場合にそれを検知せず、エラーコード 0 の正常終了としてセッションを閉じる。プロトコル違反のカプセルが正当な終了要求と同じ扱いになる。

## 現状

- `H2Session::handle_wt_close_session` は `if (length >= 4)` の内側でのみ Application Error Code を組み立て、`length < 4` では `error_code = 0` / `message_len = 0` のまま後段の検証 (1024 バイト超過・UTF-8) を通り、`SessionClosed` (error_code 0) を push してセッションを終了する
- したがって、ペイロード 0 / 1 / 2 / 3 バイトの WT_CLOSE_SESSION はいずれも「アプリケーションエラーコード 0 での正常終了」になる。draft-15 Section 6.12 の「WT_CLOSE_SESSION 無しのクリーンな終了は error code 0 と等価」と同じ観測結果になり、アプリからは違反を区別できない
- 0237 が扱ったのは可変長整数のデコード失敗である。WT_CLOSE_SESSION の Application Error Code は固定長 (4 バイト) であり `read_capsule_varint` を通らないため、0237 の経路では検知されない。固定長フィールドが欠ける経路は未対応のまま残っている
- 実測した結果: ペイロード `b"\x00\x00\x00"` (3 バイト) の WT_CLOSE_SESSION を受信すると、RST_STREAM は送出されず、`SessionClosed` (error_code 0) が push されてセッションが終了する。期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)
- `length == 4` は Application Error Message が空の正しいカプセルであり、`length > 4` はメッセージ付きの正しいカプセルである。どちらも現状どおり受理される必要がある

## 設計方針

- `length < 4` を malformed として `H2Session::reset_stream_for_malformed_capsule` で扱う。0237 と同じ後始末 (`is_terminated` / `is_established` を落として `NGHTTP2_PROTOCOL_ERROR` の RST_STREAM を送出する) を使い、`SessionClosed` を push しない
- 検証は Application Error Message の検証 (1024 バイト超過・UTF-8) より前に置く。フィールドが欠けているペイロードのメッセージを検証しても意味が無いためである
- `length == 4` と `length > 4` の挙動は変えない
- 変更対象: `src/bindings/webtransport_h2.cpp`、`tests/`

## 完了条件

- ペイロード 0 / 1 / 2 / 3 バイトの WT_CLOSE_SESSION の受信で PROTOCOL_ERROR の RST_STREAM が送出され、`SessionClosed` が push されない
- ペイロード 4 バイト (Application Error Message が空) が従来どおり受理され、`SessionClosed` の error_code がペイロードの値になる (対照)
- ペイロード 5 バイト以上で Application Error Message が従来どおり `SessionClosed` に載る (対照)
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- 識別フィールドの後に余分なバイトがあるカプセルの検証 (0241 で扱う)
- Application Error Message が 1024 バイトを超える場合と不正な UTF-8 の場合 (既存の WT_ERROR 経路を維持する)
- ピアが WT_CLOSE_SESSION を送らずに END_STREAM だけを送る場合の扱い (既存の `H2Session::handle_end_stream` を維持する)
