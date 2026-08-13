# HTTP/2 で WT_CLOSE_SESSION 受信後に close_session で応答すると SessionClosed が二重発火する

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-wt-close-session-double-close
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 で、ピアが WT_CLOSE_SESSION カプセルを送ってセッションを終了した場合に、受信側のアプリが `close_session` で応答すると SessionClosed イベントが 2 回発火する問題を修正する。closed issue 0070 の設計方針で「スコープ外の既存の挙動」として残された問題であり、`handle_wt_close_session` がエントリを削除しないことが原因。

## 現状

- `src/bindings/webtransport_h2.cpp` の `handle_wt_close_session` は SessionClosed イベントを発火して `is_terminated` / `is_established` のフラグを更新するが、`wt_sessions_` からエントリを削除しない (「process_capsules がまだバッファを参照している可能性がある」という理由で削除を先送りし、HTTP/2 ストリーム close 時の `on_stream_close_callback` に委ねている)
- コンプライアントなピアは WT_CLOSE_SESSION 送出後に必ず END_STREAM を送る (draft-ietf-webtrans-http2-15 Section 6.12 の MUST)。受信側アプリが `close_session` で応答すると自側も END_STREAM を送出し、ストリームの両ハーフが閉じて `on_stream_close_callback` が発火する
- `on_stream_close_callback` はエントリが存在する限り SessionClosed を発火するため、WT_CLOSE_SESSION 受信 (1 回目) + 両ハーフクローズ (2 回目) で SessionClosed が 2 回発火する (実測確認済み。1 回目はカプセルの error_code、2 回目は error_code 0)
- 0070 の実装 (END_STREAM 検知の `handle_end_stream`) はエントリを削除するため、END_STREAM のみの経路では二重発火しない。二重発火するのは WT_CLOSE_SESSION 受信 → アプリの `close_session` 応答 → 両ハーフクローズの経路のみ
- `process_capsules` はループ内で毎回 `get_wt_session` を再取得しており (現行実装)、`handle_wt_close_session` 内でエントリを削除しても参照切れの危険はない (レビューで確認済み。削除先送りは現状の実装では不要な防衛)

## 設計方針

- `handle_wt_close_session` で `wt_sessions_` からエントリを削除する (0070 の `handle_end_stream` と同じ「エントリ不在で塞がる」論理。`http2_stream_buffers_` の破棄も同様に行う)
- エントリ削除により、以後の `on_stream_close_callback` / `close_session` / `send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` がエントリ不在で自然に塞がる
- 削除後も `is_terminated` / `is_established` のフラグ更新は維持する (エントリ削除前に実施。エントリが存在する間の送信抑止として機能し、削除後はエントリ不在で塞がれる)
- 既存テスト `test_send_datagram_after_recv_wt_close_session_ignored` (tests/test_webtransport_h2_datagram.py) が「WT_CLOSE_SESSION 受信後に send_datagram が無視される」ことを検証しており、エントリ削除後もこの挙動が維持されることを確認する

## 完了条件

- WT_CLOSE_SESSION 受信後にアプリが `close_session` で応答しても、SessionClosed が 1 回だけ発火する (二重発火しない)
- WT_CLOSE_SESSION 受信後の `send_datagram` / `close_session` / `send_stream_data` が no-op になる (既存の挙動が維持される)
- エントリ削除が機能していることの間接検証として、WT_CLOSE_SESSION 受信後に `close_session` が no-op (WT_CLOSE_SESSION の再送出なし) になることを確認する
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用する)
