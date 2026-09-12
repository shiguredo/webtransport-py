# H2Session::reject_session が応答の submit / 送出失敗を握り潰す

- Created: 2026-09-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-reject-session-submit-failure
- Polished: 2026-09-12

## 目的

`H2Session::reject_session` は `nghttp2_submit_response` の戻り値を検査しないため、応答の submit が失敗しても呼び出し元は成功と区別できない。さらに、submit が成功しても送出時に nghttp2 が HEADERS フレームを破棄する経路があり、`on_frame_not_send_callback` が未登録のため無言で捨てられる。後者では 405 / 403 / 413 の応答がワイヤに送出されず、ストリームが応答待ちのまま滞留する (closed/0173 が解消した症状と同じ観測結果になる)。`accept_session` は `rv` を検査して失敗時に `false` を返しており、`reject_session` も submit 失敗と送出失敗を検出して観測可能にする。あわせて、HTTP/2 のストリーム ID として無効な 0 以下を入力検証で拒否する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reject_session` は `nghttp2_submit_response` の戻り値を捨てている
- `session_id <= 0` を渡すと `nghttp2_submit_response` は `NGHTTP2_ERR_INVALID_ARGUMENT` を返す (`_deps/nghttp2/v1.70.0/source/lib/nghttp2_submit.c` の `submit_response_shared` が `stream_id <= 0` で返す)。現行実装はこれを握り潰し、ワイヤ送出なし・イベントなし・接続は閉じないまま黙って no-op になる (実験で確認済み: `reject_session(0, 403)` / `reject_session(-1, 403)` はいずれも `send()` が None、イベント 0 件)
- submit が成功しても送出時に落ちる経路がある。HEADERS は `nghttp2_session_add_item` がストリーム状態を検査せずキューへ積むため、同一ストリームへの 2 回目の応答は submit 時点では成功し、送出時に `NGHTTP2_ERR_STREAM_SHUT_WR` で破棄される。リセット済み・クローズ済みストリームへの応答も送出時に `NGHTTP2_ERR_STREAM_CLOSED` で破棄される。`on_frame_not_send_callback` が未登録のため、いずれも無言で捨てられる (実験で確認済み: 同一セッションへ 403 を 2 回で `send()` は 1 通のみ・イベント 0 件、両ハーフクローズ後は `send()` が None・イベント 0 件)
- `NGHTTP2_ERR_NOMEM` など他の submit 失敗も同様に観測できない
- 対照: `H2Session::accept_session` は `rv != 0` のとき蓄積を破棄して `false` を返す
- 呼び出し元は `H2Session::on_frame_recv_callback` (webtransport-init 不正の 400 / 非 WT リクエストの 405) と `on_data_chunk_recv_callback` (過大カプセルの 413)、高レベル `h2.Server.on_session_request` (任意の 3xx-5xx)。いずれも正のストリーム ID を渡す
- 高レベル `h2.Server` / `h2.Client` は `H2EventType::Error` のうち `error_code == 0x50` のみを `on_error` へ渡す

## 設計方針

- 入力検証: 接続ガード (`!session_ || !is_server_`) の直後、status_code の検証と同じ位置で `session_id <= 0` を `std::invalid_argument` にする (nanobind の既定翻訳で `ValueError`)。closed/0135 の「関数冒頭 (接続ガードの直後)」の先例に揃える。クライアントセッションでの呼び出しは従来どおりガードで no-op のままとする
- submit 失敗: `nghttp2_submit_response` の戻り値が非 0 のときは `H2EventType::Error` イベントを push し、失敗を観測可能にする。`session_id` は対象セッション、`error_code` は `static_cast<uint32_t>(-rv)`、`error_message` は `nghttp2_strerror(rv)` とする (`receive()` の失敗時の先例と同じ)
- 送出失敗: `nghttp2_session_callbacks_set_on_frame_not_send_callback` を登録し、HEADERS フレームが送出されなかった場合に同じ形式の `H2EventType::Error` を push する (`error_code` は `static_cast<uint32_t>(-lib_error_code)`、`error_message` は `nghttp2_strerror(lib_error_code)`)。これにより、同一ストリームへの再応答 (`STREAM_SHUT_WR`) とリセット済みストリームへの応答 (`STREAM_CLOSED`) が観測可能になり、回帰テストで発火を検証できる。イベントは `reject_session` 呼び出し時点ではなく、nghttp2 が次にフレームを送出する `send()` / `receive()` の中で push される。コールバック内では nghttp2 API を呼ばず `push_event` のみを行い、戻り値は 0 とする (非 0 は `NGHTTP2_ERR_CALLBACK_FAILURE` として致命扱いになるため。再入防止)
- 観測点は低レベル `h2.Session.next_event()` とする。高レベル `h2.Server` / `h2.Client` は 0x50 以外の Error を `on_error` へ渡さないため、本 Error は高レベルには届かない (高レベルへの通知追加は対象外)
- submit / 送出失敗時のセッション状態は成功時と同じにする。非 2xx は `wt_sessions_` を削除する (エントリを残すと `send_datagram` / `send_stream_data` が開いたままカプセルが滞留し、非確立セッションでは `SessionClosed` を発火しない設計ピンも壊れる)。2xx は `is_terminated` と `capsule_buffer` の破棄を行う
- 失敗後も応答は送出されないためストリームは滞留したまま (nghttp2 への RST_STREAM 送出は行わない) を既知の制約とする
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (`reject_session` の docstring に session_id 検証と Error 観測を追記) / `tests/test_webtransport_h2_reject_session.py` (回帰テスト) / `tests/prop_webtransport_h2.py` (reject_session PBT の docstring が「セッション未確立等は無視される」前提のため更新) / `CHANGES.md` の develop への FIX エントリ

## 完了条件

- サーバーセッションに対する `reject_session(0, status)` と `reject_session(-1, status)` が `ValueError` になること (クライアントセッションでは従来どおり no-op であること)
- submit 失敗 (`rv != 0`) のときに `H2EventType::Error` を push する実装であること (`NGHTTP2_ERR_NOMEM` は公開 API から再現不能のため自動テストの対象外とし、コードで担保する)
- 送出失敗の回帰テスト: 同一ストリームへ `reject_session` を 2 回呼んだとき 2 回目、またはリセット済みストリームへ呼んだときに `H2EventType::Error` が発火すること
- 失敗時も非 2xx の `wt_sessions_` 削除が行われ、`send_stream_data` / `send_datagram` がエントリ不在で塞がれることをテストでピン留めすること
- `tests/prop_webtransport_h2.py` の reject_session PBT の docstring を更新すること
- `CHANGES.md` の `## develop` に FIX エントリが追加されていること
- 既存のテストが引き続き通過すること
