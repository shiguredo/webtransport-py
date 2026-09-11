# H2Session::reject_session が nghttp2_submit_response の失敗を握り潰す

- Created: 2026-09-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-reject-session-submit-failure
- Polished: {YYYY-MM-DD}

## 目的

`H2Session::reject_session` は `nghttp2_submit_response` の戻り値を検査しないため、応答の submit が失敗しても呼び出し元は成功と区別できない。失敗時は 405 / 403 / 413 の応答がワイヤに送出されず、ストリームが応答待ちのまま滞留する (closed/0173 が解消した症状と同じ観測結果になる)。`accept_session` は `rv` を検査して失敗時に `false` を返しており、`reject_session` も失敗を検出して観測可能にする。あわせて、HTTP/2 のストリーム ID として無効な 0 以下を入力検証で拒否する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reject_session` は `nghttp2_submit_response` の戻り値を捨てている
- `session_id <= 0` を渡すと `nghttp2_submit_response` は `NGHTTP2_ERR_INVALID_ARGUMENT` を返す (`_deps/nghttp2/v1.70.0/source/lib/nghttp2_submit.c` の `submit_response_shared` が `stream_id <= 0` で返す)。現行実装はこれを握り潰し、ワイヤ送出なし・イベントなし・接続は閉じないまま黙って no-op になる (実験で確認済み: `reject_session(0, 403)` / `reject_session(-1, 403)` はいずれも `send()` が None、イベント 0 件)
- `NGHTTP2_ERR_NOMEM` など他の submit 失敗も同様に観測できない
- 対照: `H2Session::accept_session` は `rv != 0` のとき蓄積を破棄して `false` を返す
- 呼び出し元は `H2Session::on_frame_recv_callback` (webtransport-init 不正の 400 / 非 WT リクエストの 405) と `on_data_chunk_recv_callback` (過大カプセルの 413)、高レベル `h2.Server.on_session_request` (任意の 3xx-5xx)

## 設計方針

- `H2Session::reject_session` の冒頭で `session_id <= 0` を検査し、`std::invalid_argument` を投げる (nanobind の既定翻訳で `ValueError`)。`H2Session::reset_stream` の varint 検査と同じ流儀とする
- `nghttp2_submit_response` の戻り値が非 0 のときは `H2EventType::Error` イベントを push し、失敗をアプリから観測可能にする
- submit 失敗時のセッション状態 (非 2xx の `wt_sessions_.erase` を実行するか) は、失敗を観測できることを前提に実装時に確定する。少なくとも「送出できていないのに成功したように扱う」経路を残さない
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` / `tests/` / `CHANGES.md` の develop への FIX エントリ

## 完了条件

- `reject_session(0, status)` と `reject_session(-1, status)` が `ValueError` になること
- submit 失敗時に `H2EventType::Error` イベントが発火すること (0 以下の入力は入力検証で拒否されるため、submit 失敗の観測はイベント push の実装と通常経路の回帰テストで確認する)
- 上記の回帰テストを追加すること
- `CHANGES.md` の develop に FIX エントリが追加されていること
- 既存のテストが引き続き通過すること
