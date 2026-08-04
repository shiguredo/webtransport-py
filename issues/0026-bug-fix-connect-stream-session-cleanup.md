# CONNECT ストリームのリセット時にセッション終了の後始末が行われないのを修正する

- Created: 2026-08-04
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-connect-stream-session-cleanup
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」でセッションが終了するにもかかわらず、後始末が一切行われない問題を修正する。CONNECT ストリームのリセットでセッションが終了しても、セッション ID が管理集合に残り続け、アプリケーションへの終了通知 (`on_session_closed`) が発火しない。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` は CONNECT ストリームのリセット時に `session_ids_` から削除しない (削除は `close_session` / `recv_wt_close_session_cb` のみ)
- `H3Session::recv_wt_close_session_cb` は WT_CLOSE_SESSION カプセル専用のため、CONNECT ストリームのリセットでは呼ばれない。`stream_close_cb` も CONNECT ストリームのセッション ID を復元できない (CONNECT ストリームは `stream_info_` に登録されない)
- そのため、CONNECT ストリームのリセットでセッションが終了しても `SessionClosed` イベントが生成されず、高レベル `Server` の `on_session_closed` は永遠に呼ばれない
- 死んだセッション ID が `session_ids_` に残り続けるため、`get_session_ids()` が不正確になり、`send_stream_data` のフォールバック (`src/bindings/webtransport_h3.cpp` の `send_stream_data`。`session_ids_` の先頭要素) や DATAGRAM フォールバックが死んだセッションを選び得る
- 0009 の CONNECT ストリームのフォールバックは「CONNECT ストリームのリセット時に `session_ids_` から削除されない挙動」に依存しているため、本 issue の対応後はフォールバックの見直しが必要になる
- draft-ietf-webtrans-http3-16 Section 6 はセッション終了時に「the endpoint MUST reset the send side and abort reading on the receive side of all unidirectional and bidirectional streams associated with the session using the WT_SESSION_GONE error code」も要求するが、これも未対応

## 設計方針

- CONNECT ストリームのリセット (close_stream 経路) でセッション終了を検知し、`session_ids_` から削除して `SessionClosed` イベントを発火する
- クリーンクローズ (FIN) 経路の検知は 0010 の設計方針で「対象外」とされている (H3Session は end_stream コールバックを登録していない) ため、FIN 検知を含めるかは実装時に判断する (含める場合は別途 end_stream コールバックの追加が必要)
- 0009 の CONNECT ストリームのフォールバック (close_stream の戻り値復元) は本 issue の対応後に見直す (`session_ids_` から削除された CONNECT ストリームのリセットでは -1 が返るようになるため、フォールバックとテストの整合を確認する)
- draft-ietf-webtrans-http3-16 Section 6 の MUST (関連ストリームの WT_SESSION_GONE でのリセット) への対応有無は、0010 の CONNECT リセット経路のバッファ削除との関連を含めて実装時に判断する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (セッション終了の検知と後始末)、`src/webtransport/h3/server.py` (必要に応じて)、テスト (`tests/test_e2e_webtransport_h3.py`。0009 の低レベル API クライアント構成を流用する)

## 完了条件

- CONNECT ストリームのリセットで `session_ids_` から削除され、`SessionClosed` イベントが発火し、高レベル `Server` の `on_session_closed` が呼ばれる
- モックなしの e2e テストで検証できる (複数セッションを確立し、1 つの CONNECT ストリームをリセットして、該当セッションのみが終了扱いになることを確認する)
- 0009 の CONNECT ストリームのフォールバックとテストが本 issue の対応後に整合する (既存テストが引き続き通る)
