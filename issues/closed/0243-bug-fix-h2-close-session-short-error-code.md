# h2 で Application Error Code が 4 バイト未満の WT_CLOSE_SESSION を正常終了として受理する

- Created: 2026-09-20
- Completed: 2026-09-21
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
- 変更対象: `src/bindings/webtransport_h2.cpp`、`src/bindings/webtransport_h2.h` (コメント更新のみ。挙動の変更は無い)、`tests/test_webtransport_h2_close_session_message.py`。実装時に次の 2 ファイルも対象になった
  - `src/webtransport/h2/server.py` / `src/webtransport/h2/client.py`: `send_datagram` の docstring が終了経路を列挙しており、C++ 層の同じ列挙にペイロード不正のカプセル経路を足すと層間で食い違うため、列挙を合わせる (docstring のみ)
- テストの書き方についての補足:
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

## 解決方法

- `H2Session::handle_wt_close_session` の冒頭で `length < 4` を検査し、`H2Session::reset_stream_for_malformed_capsule` で PROTOCOL_ERROR の RST_STREAM を送出して早期 return する。Application Error Code は draft-15 Section 6.12 の 32 ビット固定フィールドであり `read_capsule_varint` の検証を通らないため、固定長フィールドの欠落を RFC 9297 Section 3.3 の「定義が挙げるフィールドの終端より前に終わるペイロード」として扱う。検証は Application Error Message の検証 (1024 バイト超過・UTF-8) より前に置いた (フィールドが欠けているペイロードのメッセージを検証しても意味が無い)
- `length >= 4` の入れ子を早期 return に置き換え、`error_code` / `message_bytes` / `message_len` を const かつ無条件に組み立てる形にした。`length == 4` では `message_bytes` が終端を指すだけで参照しない (`message_len` が 0 のため検証と組み立てを素通りする) ことをコメントに残した。`length == 4` と `length > 4` の受理、応答の END_STREAM、エントリ削除、バッファ破棄は変えていない
- 終了検知の説明を「正常な WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後・非 2xx 拒否受信後はエントリが削除されて塞がり、ペイロード不正のカプセル (Application Error Code が欠けた WT_CLOSE_SESSION など) とローカル close_session 後は `is_terminated` で塞がる」に書き分けた (`WtSessionInfo::is_terminated` / `send_datagram` / `close_session` の docstring と、`handle_end_stream` / `send_stream_data` / `stop_sending` / `send_datagram` / `close_session` / `drain_session` のコメント)。`send_datagram` の高レベル API の docstring も同じ列挙に合わせた (挙動の変更は無い)
- `tests/test_webtransport_h2_close_session_message.py` を 11 件に拡張した
  - ペイロード 0 / 1 / 2 / 3 バイトで PROTOCOL_ERROR の RST_STREAM が送出され、`SessionClosed` (error_code は HTTP/2 の PROTOCOL_ERROR) が 1 回だけ通知され、Error イベントと応答の WT_CLOSE_SESSION が出ないことを検証する (parametrize 4 件)
  - ペイロード 4 バイト (メッセージ空、error code は 0x01020304) が受理され、error code がペイロードの値として `SessionClosed` に載ることを検証する (対照。0 では表明が空虚になり、4 バイトすべてを異なる値にしてバイト順の誤りも検出する)
  - ペイロード 5 バイト以上で Application Error Message が `SessionClosed` に載ることを検証する (対照)
  - 受理側の表明は `_assert_accepted_without_error_signals(server, session_id)` に集約した。`server.send()` を 1 回だけ呼び、同一の wire に対して「応答の END_STREAM がある (draft-15 Section 6.12 の受信者 MUST)」「PROTOCOL_ERROR の RST_STREAM が無い」「WT_CLOSE_SESSION が無い」を表明する。以前の `_assert_no_wt_error_sent` は wire が空でも通るため、受理と誤リセットが同時に起きる実装を検出できなかった
  - 4 バイト big-endian の組み立ては `_encode_wt_close_session_payload` に集約し、4 バイト未満は生のペイロードを `_inject_close_session_payload` へ渡す
- RED は 2 通りで実測した: `H2Session::handle_wt_close_session` を develop 版に戻すと短いペイロードの 4 件が失敗し対照 7 件は通り、`length == 4` でも reset する変異では 4 バイト対照テストが失敗する
- 全テストが通過する (1308 passed。環境依存で skip される 1 件は今回の実行では通過した)
- 設計方針との差異: 応答の WT_CLOSE_SESSION が送出されない側の表明は、既存の `_assert_no_wt_error_sent` ではなく `_assert_accepted_without_error_signals` を新設した (理由は上記)。RST_STREAM フレームのヘルパは 0237 のローカル実装ではなく、0241 で `tests/conftest.py` に集約済みの `_encode_rst_stream_frame` / `_assert_session_closed_by_protocol_error` / `_WT_CLOSE_SESSION_TYPE_BYTES` を import した。したがって本テストファイルを 0248 の変更対象に追加する必要は無い (本ファイルにローカル定義は無い)
- WT_CLOSE_SESSION のカプセル組み立てヘルパの重複 (複数ファイル) は 0248 の対象 (RST_STREAM フレーム生成) ではなく、カプセル種別の生リテラルは 0239 が扱うため、新規 issue は起票しない
- `CHANGES.md` は CODEBASE.md の指示により更新しない。公開 API (nanobind) の変更は無く、型スタブの再生成も不要
