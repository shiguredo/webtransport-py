# 遅延クローズ保留中に未送信の 2xx レスポンスが送出される

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-deferred-close-stale-2xx
- Polished: 2026-08-12

## 目的

受理前 FIN の遅延クローズ保留中にセッション終了 (WT_CLOSE_SESSION 受信) が発生した場合、終了済みセッションの CONNECT ストリームに未送信の 2xx レスポンスが後から書き出される問題を修正する。終了を学習したセッションへの無意味な送信をなくす。

## 現状

- 0058 の遅延クローズは、未送信の 2xx を破棄しないため 2xx レスポンスの書き出し完了 (`stream_flushed`) を待ってから `close_stream` を実行する。フロー制御等で 2xx が書き出せない間は保留される (`block_stream` で再現)
- 保留中に `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) でセッション終了が発生した場合 (Sans-IO 構成で実測確認済み):
  - セッション終了自体は 1 回だけ検知される (`close_session` 経路と同様に `session_ids_` から削除されるため、遅延クローズの `close_stream` は CONNECT ストリーム判定が成立せず SessionClosed は再発火しない)
  - しかし、その後の `get_streams_to_send` は nghttp3 の送信キューに残った 2xx (実測で 33 バイト。QPACK 状態により変動する) を書き出す。このとき遅延クローズループの `close_stream` も実行され、`stream_close_cb` が発火して STREAM_CLOSED イベント (session_id = -1) が積まれる
  - ピア (クライアント) は受信した 2xx を黙って消費する (CONNECT ストリームの状態が IGN_REST のため。H3_MESSAGE_ERROR のリセットは発生しない)
- 保留中にローカル `close_session` (WT_CLOSE_SESSION 送出) が発生した場合 (実測確認済み):
  - WT_CLOSE_SESSION カプセルは nghttp3 の送信キューで 2xx の後ろに積まれ、2xx + カプセルが一体 (実測で 45 バイト。2xx 部分は QPACK 状態により変動する) で書き出される。ピアは 2xx を受信後に WT_CLOSE_SESSION を受信して終了を学習する (実害は 2xx の無駄な送信のみ)
- draft-ietf-webtrans-http3-16 Section 6 の「新しいデータグラム・ストリームの禁止」MUST には反しない (既存 CONNECT ストリームへの 2xx 送信のため)。「If any additional stream data is received on the CONNECT stream after receiving a WT_CLOSE_SESSION capsule, the stream MUST be reset with code H3_MESSAGE_ERROR」は受信側の対処であり、本シナリオでは適用されない (受信側が WT_CLOSE_SESSION を受信済みでないため)。本対応の根拠は、終了を学習したセッションへの無意味な送信の排除である

## 設計方針

- 未送信 2xx の破棄手段は既存の `close_stream` (`nghttp3_conn_close_stream`) を流用する。nghttp3 には 2xx のみをキャンセルする API は存在せず、`close_stream` がストリームの送信キュー全体 (未送信 2xx を含む) を破棄する唯一の手段である (Sans-IO 構成で実測確認済み: 保留中に `close_stream` を実行すると、ブロック解除後の `get_streams_to_send` は空になる)
- `recv_wt_close_session_cb` 経路: セッション終了検知時に、そのセッションが `pre_accept_fin_accepted_session_ids_` に含まれる場合、`close_stream(session_id, 0)` を実行して未送信 2xx を破棄し、`pre_accept_fin_accepted_session_ids_` からエントリを除去する (除去しないと、存在しないストリームは `stream_flushed` が 1 を返すため、次の `get_streams_to_send` の遅延クローズループで 2 回目の `close_stream` が実行され、STREAM_CLOSED イベントの個数が実装依存になる)。ただし `recv_wt_close_session_cb` は `nghttp3_conn_read_stream2` の処理中に同期発火するため、コールバック内で nghttp3 を呼ぶと再入になる。検知したセッション ID を保留集合に記録し、`receive_stream_data` が `nghttp3_conn_read_stream2` から戻った後に実行する (`pending_fin_session_ids_` と同じパターン。read_stream2 がエラーを返した場合も処理する)
- `close_session` 送出経路: WT_CLOSE_SESSION カプセルと 2xx が同一の nghttp3 送信キューにあり、2xx のみを破棄する手段がない。`close_stream` で破棄するとカプセル (error code / message) も失われ、ピアに終了情報が伝わらないため、送出経路は 2xx の送出を許容する (既知の制約。ピアは受信した 2xx を IGN_REST で無視する)
- `close_stream` 実行時の副作用: `stream_close_cb` が発火して STREAM_CLOSED イベント (session_id = -1、error_code は渡した 0) が積まれる (既存の遅延クローズでも同様)。SessionClosed は発火しない (`session_ids_` から削除済みのため)。セッションに属するデータストリームが残存する場合は、`nghttp3_conn_close_stream` が WT_SESSION_GONE でそれらをシャットダウンし、STOP_SENDING / RESET_STREAM イベント (session_id = -1) も積まれ得る。これらのイベントは許容する
- 遅延クローズループ (`get_streams_to_send`) に「`session_ids_` に含まれる場合のみ `close_stream`」条件を追加する案は不採用とする: 2xx の書き出しは writev ループが行い、遅延クローズループの条件変更では止まらないため
- 破棄の対象は `pre_accept_fin_accepted_session_ids_` に含まれる (受理済みで 2xx が発生し得る) セッションに限定する。`pending_pre_accept_fin_session_ids_` のみ (検知済み・未受理) のセッションは 2xx が未発生のため破棄対象がなく、対象外とする (WT_CLOSE_SESSION 受信後も保留エントリは既存どおり残る)
- 遅延クローズ機構 (`pre_accept_fin_accepted_session_ids_` と `get_streams_to_send` の `stream_flushed` 確認) は issue 0064 (QPACK ブロック中の受理前 FIN 検知) の修正対象と同一である。0064 が先に実装される場合、本 issue の対応 (recv 経路での破棄) は 0064 の検知経路から流入するエントリにも同様に適用される。本 issue が先に実装される場合は、0064 の実装時にこの破棄処理との整合 (エントリの除去タイミング等) を確認する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (recv 経路の破棄と保留集合)、テスト (既存の `test_pre_accept_fin_wt_close_session_during_deferred_close` の拡張と docstring の「既知の制約」記述の書き換え)、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 遅延クローズ保留中に WT_CLOSE_SESSION を受信しても、ブロック解除後の `get_streams_to_send` に未送信の 2xx が現れない
- 通常の受理前 FIN の遅延クローズ (2xx 書き出し完了後に `close_stream`) は影響を受けない
- ローカル `close_session` 送出経路では 2xx が送出される既知の制約をピン留めする (テストでサーバー側の送出 (2xx + カプセルが一体で出ること) と、クライアント側の受信 (2xx を IGN_REST で消費し、WT_CLOSE_SESSION による SessionClosed が発火すること) の現状の挙動を固定する)
- モックなしの Sans-IO テストで検証できる (block_stream で 2xx の書き出しを止め、WT_CLOSE_SESSION を受信した後に unblock_stream して、`get_streams_to_send` に 2xx が出ないことを確認する構成。既存テスト `test_pre_accept_fin_wt_close_session_during_deferred_close` を拡張する)
