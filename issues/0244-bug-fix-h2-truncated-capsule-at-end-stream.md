# h2 でクリーンな END_STREAM の直前に切り詰められたカプセルが検証されない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-truncated-capsule-at-end-stream
- Polished: {YYYY-MM-DD}

## 目的

RFC 9297 Section 3.3 の第 3 段落は「カプセルを運ぶストリームの受信側がクリーンに終了し、ストリーム上の最後のカプセルが切り詰められていた場合、malformed または incomplete なメッセージとして扱わなければならない (MUST)」と定める。`src/bindings/webtransport_h2.cpp` の `H2Session::handle_end_stream` は END_STREAM を受信した時点で未処理のカプセルバッファを検査しないため、カプセルの途中でストリームが終わっても正常終了 (error code 0) として扱われる。ピアの送信が途中で切れたことを検知できない。

## 現状

- `H2Session::process_capsules` はカプセルの Type / Length / ペイロードが揃わない分を `WtSessionInfo::capsule_buffer` に残し、後続の DATA を待つ
- `H2Session::handle_end_stream` は `WtSessionInfo::capsule_buffer` を参照せず、`SessionClosed` (error_code 0) を push してエントリ (`wt_sessions_`) と送信バッファ (`http2_stream_buffers_`) を破棄する。残っていた `capsule_buffer` はエントリごと破棄されるため、切り詰めは観測されないまま消える
- 0237 は「ペイロードが揃ったカプセルの不完全な可変長整数」、0241 は「識別フィールドの後に余分なバイトがあるカプセル」を扱う。本 issue は「ストリームが終了した時点で最後のカプセルがまだ途中だった」場合であり、`read_capsule_varint` を通らない入力である
- 実測した結果: 次のいずれも RST_STREAM を送出せず、`SessionClosed` (error_code 0) を push してセッションが終了する
  - Length が宣言済みでペイロードが途中の WT_MAX_DATA (`Length = 8` に対して 4 バイトのみ) を含む DATA を送り、続けて END_STREAM を送る
  - WT_MAX_DATA の Type だけを送り、続けて END_STREAM を送る
  - 期待は RFC 9297 Section 3.3 の malformed としての扱い (RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリームエラー)
- カプセル境界で END_STREAM が届いた場合 (バッファが空) は、draft-15 Section 6.12 の「WT_CLOSE_SESSION 無しのクリーンな終了は error code 0 と等価」に従う現状の挙動が正しい

## 設計方針

- `H2Session::handle_end_stream` で `WtSessionInfo::capsule_buffer` が空でない場合を malformed として扱い、`H2Session::reset_stream_for_malformed_capsule` を通す。0237 の後始末と同じく `is_terminated` / `is_established` を落として `NGHTTP2_PROTOCOL_ERROR` の RST_STREAM を送出し、`SessionClosed` を直接 push しない (RST_STREAM の送出で発火する `on_stream_close_callback` が `SessionClosed` を通知する)
- 検査位置は `is_established` / `is_terminated` の判定より後にし、`SessionClosed` の push とエントリ破棄より前に置く
- バッファが空の場合は現状の正常終了を維持する (エントリ破棄・送信バッファ破棄・`SessionClosed` の error_code 0)
- ピアが WT_CLOSE_SESSION を送ってから END_STREAM を送る経路は、`H2Session::handle_wt_close_session` がエントリを削除済みのため `H2Session::handle_end_stream` の確立判定で早期 return する (現状維持)。本 issue の対象は END_STREAM のみで終わるセッションである
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h`、`tests/`

## 完了条件

- カプセルの途中で END_STREAM を受信した場合、PROTOCOL_ERROR の RST_STREAM が送出される
- 同じ入力で `SessionClosed` が直接 push されず、RST_STREAM の送出に伴うストリーム終了で `SessionClosed` (error_code は HTTP/2 の PROTOCOL_ERROR) が 1 回だけ通知される (0237 のテストと同じ観測方法)
- カプセル境界で END_STREAM を受信した場合 (空のカプセルバッファ) は従来どおり `SessionClosed` (error_code 0) が通知される (対照)
- カプセルの Type のみ / Length のみ / ペイロード途中の 3 通りの切り詰め位置で検証する
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- Application Error Code が 4 バイト未満の WT_CLOSE_SESSION の扱い (0243 で扱う)
- 識別フィールドの後に余分なバイトがあるカプセルの検証 (0241 で扱う)
- ペイロードが揃ったカプセルの値の意味論 (単調性・ストリーム状態など) の既存検証 (本 issue では変更しない)
- 「WT_CLOSE_SESSION 無しの END_STREAM に対する応答の END_STREAM 送出」(既知の制約として現状維持)
