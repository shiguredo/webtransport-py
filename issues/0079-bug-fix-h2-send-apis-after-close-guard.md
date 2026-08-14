# HTTP/2 の stop_sending / drain_session が終了済みセッション宛にカプセルを残留させる問題を修正する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-send-apis-after-close-guard
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 の `stop_sending` / `drain_session` は `get_wt_session` を確認せず `send_capsule` を呼ぶため、セッション終了 (エントリ削除) 後に呼ぶと `http2_stream_buffers_` にカプセルが残留してメモリを保持し続ける。エントリ不在で自然に塞がる他の送信 API と揃えて no-op にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `stop_sending` と `drain_session` は `get_wt_session` を確認せず `send_capsule` する (エントリ不在でもカプセルをキューする)
- セッション終了経路 (WT_CLOSE_SESSION 受信 / ピアの END_STREAM 受信) でエントリが削除された後にこれらを呼ぶと、`send_capsule` が消えた `http2_stream_buffers_` エントリを再生成し、`nghttp2_session_resume_data` はストリーム不在で失敗するため、カプセルはワイヤに送出されず残留する
- 終了処理ハンドラ (`handle_wt_close_session` / `handle_end_stream`) のコメントでは「stop_sending / drain_session は get_wt_session を確認せずカプセルを送出するため塞がれないが、対象外」と明記されている
- 他の送信 API (`send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` / `close_session`) は `get_wt_session` の確認でエントリ不在時に塞がれる

## 設計方針

- `stop_sending` / `drain_session` の冒頭に `get_wt_session` の確認を追加し、エントリ不在時は no-op にする (他の送信 API と揃える)
- 終了処理ハンドラのコメントの「対象外」の記述を更新する (塞がれるようになるため)
- テスト: セッション終了後に `stop_sending` / `drain_session` を呼んでもカプセルが残留しないこと (ワイヤに送出されないこと) を検証する

## 完了条件

- セッション終了後の `stop_sending` / `drain_session` が no-op になり、`http2_stream_buffers_` に残留しない
- 全テストが通る
