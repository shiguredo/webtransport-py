# h2 でクリーンな END_STREAM の直前に切り詰められたカプセルが検証されない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-truncated-capsule-at-end-stream
- Polished: 2026-09-20

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
- エントリと送信バッファの破棄は `H2Session::on_stream_close_callback` に任せる (`wt_sessions_` / `http2_stream_buffers_` / `end_stream_pending_` / `pending_headers_` / 未消費の受信バイト記録を解放する既存経路)。malformed と判定した場合は `SessionClosed` の push とエントリ破棄を行わずに戻るため、二重解放は起きない
- 検査位置は `is_established` / `is_terminated` の判定より後にし、`SessionClosed` の push とエントリ破棄より前に置く。確立前 (受理前・非 2xx 拒否) のセッションはこの判定で早期 return するため、切り詰めの検証対象にならない (セッションとして成立していない入力であり、既存の挙動を維持する。サーバー側の受理前 FIN ではエントリが残留し、その後の `H2Session::accept_session` でも切り詰めは再検査しない)
- バッファが空の場合は現状の正常終了を維持する (エントリ破棄・送信バッファ破棄・`SessionClosed` の error_code 0)
- ピアが WT_CLOSE_SESSION を送ってから END_STREAM を送る経路は、`H2Session::handle_wt_close_session` がエントリを削除済みのため `H2Session::handle_end_stream` の確立判定で早期 return する (現状維持)。本 issue の対象は END_STREAM のみで終わるセッションである
- 変更対象: `src/bindings/webtransport_h2.cpp`、`src/bindings/webtransport_h2.h` (コメント更新のみ。挙動の変更は無い)、`tests/test_webtransport_h2_end_stream.py` (END_STREAM のテスト。既存の `_encode_capsule` / `_encode_data_frame` / `_drain_events` をそのまま使える)、`tests/test_webtransport_h2_recv_flow_control.py` (既存テストの前提変更。下の「既存テストの前提が 1 件変わる」を参照)。RST_STREAM の検出は 0237 のローカルヘルパ (`tests/test_webtransport_h2_incomplete_capsule_payload.py` の `_encode_rst_stream_frame` と `_assert_session_closed_by_protocol_error`) に倣う。0248 の 変更対象は現時点で `tests/conftest.py` と既存 3 テストファイルであり、本 issue で新たに追加する `tests/test_webtransport_h2_end_stream.py` のローカルヘルパを含まないため、本 issue を先に実装してローカルヘルパを増やす場合は 0248 の磨き上げ時に範囲へ追加する
- 前提が変わる既存の記述 (コメント 3 箇所) とテスト (1 件) をあわせて直す
  - `H2Session::reset_stream_for_malformed_capsule` の「capsule_buffer は触らない (呼び出し元の process_capsules のループが is_terminated を見て破棄する)」は、`H2Session::handle_end_stream` から呼ぶ経路では成立しない (破棄はエントリごと `on_stream_close_callback` で行われる)。呼び出し元ごとの違いを書く
  - `WtSessionInfo::is_terminated` のコメント (`src/bindings/webtransport_h2.h`) は終了理由の列挙に「END_STREAM 時点でカプセルが途中だったこと (フレーミングの欠落)」を追加する
  - `H2Session::handle_end_stream` の既存コメントは「WT_CLOSE_SESSION 無しの END_STREAM は常に error code 0 で終了する」を前提にしているため、カプセルが途中の場合は RST_STREAM になることを追記する
- **既存テストの前提が 1 件変わる**: `tests/test_webtransport_h2_recv_flow_control.py` の `test_peer_end_stream_releases_unconsumed_recv_bytes` は、未完成カプセル (宣言長 16 に対してペイロード 4 バイト) を注入した直後に空の DATA + END_STREAM を送り、`H2Session::handle_end_stream` 自身の `discard_stream_recv_bytes` による解放を検証している。本修正後はこの入力が malformed になり、解放は RST_STREAM に伴う `on_stream_close_callback` で起きるため、表明は通るが根拠が変わる。docstring を「解放は RST_STREAM に伴う `on_stream_close_callback` で起きる」に合わせ、このファイルも変更対象に含める
  - なお `H2Session::handle_end_stream` の `discard_stream_recv_bytes` を直接検証することはできない。完成したカプセルは `H2Session::process_capsule` の冒頭で `H2Session::consume_recv_bytes` にワイヤ長を消費させるため、カプセル境界で END_STREAM が届いた時点で未消費の記録は既に `None` であり、この解放は観測不能な防御的呼び出しになる。クリーンな END_STREAM の対照は `SessionClosed` (error_code 0) の通知で固定する

## 完了条件

- カプセルの途中で END_STREAM を受信した場合、PROTOCOL_ERROR の RST_STREAM が送出される (WT_CLOSE_SESSION 送信済み・ローカル `close_session` 済み・確立前のセッションは設計方針の早期 return により対象外)
- 同じ入力で `SessionClosed` が直接 push されず、RST_STREAM の送出に伴うストリーム終了で `SessionClosed` (error_code は HTTP/2 の PROTOCOL_ERROR) が 1 回だけ通知される (0237 のテストと同じ観測方法)
- カプセル境界で END_STREAM を受信した場合 (空のカプセルバッファ) は従来どおり `SessionClosed` (error_code 0) が通知される (対照)
- カプセルの Type のみ / Length のみ / ペイロード途中の 3 通りの切り詰め位置で検証する
- 各テストは追加した検証を外すと失敗することを実測で確認する (RED。カプセル境界で END_STREAM を送る対照テストは現行実装でも通るため RED の対象外とする)
- 全テストが通過する

## 対象外

- Application Error Code が 4 バイト未満の WT_CLOSE_SESSION の扱い (0243 で扱う)
- 識別フィールドの後に余分なバイトがあるカプセルの検証 (0241 で扱う)
- ペイロードが揃ったカプセルの値の意味論 (単調性・ストリーム状態など) の既存検証 (本 issue では変更しない)
- 確立前 (受理前・非 2xx 拒否・サーバー側の受理前 FIN) のセッションで切り詰められたカプセル (早期 return により検証しない。受理前 FIN の場合はエントリが残留し、その後に `H2Session::accept_session` すると確立済みかつ切り詰め未検証のセッションが残り得るが、本 issue の対象外とする)
- 「WT_CLOSE_SESSION 無しの END_STREAM に対する応答の END_STREAM 送出」(既知の制約として現状維持)
