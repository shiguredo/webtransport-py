# CONNECT ストリームの受理前 FIN でセッション終了が検知されない

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-pre-accept-connect-fin
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」のうち、CONNECT リクエストのヘッダーと FIN が同一読み取りで到着した場合（受理前 FIN）にセッション終了の検知が成立しない問題を修正する。FIN 経路のセッション終了検知自体は実装済みだが、受理前 FIN では nghttp3 の挙動により検知が成立せず、セッション ID が管理集合 `session_ids_` に残り続ける。

## 現状

- リクエストヘッダーと FIN が同一読み取りで到着した場合、nghttp3 は応答送信前のストリームを WT_SESSION_BLOCKED にして空 FIN を処理しないため、`end_stream` コールバックが発火しない (nghttp3 の read_bidi は WT_SESSION_BLOCKED 中に srclen == 0 なら早期 return し、ヘッダー処理後も「Server has not submitted response」の分岐で blocked を立てて早期 return する)
- `accept_session` によるセッション受理後も、process_blocked_wt_stream_data は inq が空 (空 FIN はバッファされない) のため `end_stream` は発火せず、FIN は喪失する。QUIC 層は fin を 1 回しか渡さないため、リトライで復元されることもない
- 結果として、セッションは確立される (SESSION_READY は発火し、`accept_session` は成功する) が、`SessionClosed` イベントは発火せず、セッション ID が `session_ids_` に残り続ける (接続終了まで)
- クライアント側の 200 レスポンスと FIN の同一読み取りは正常に検知できる (`end_headers_cb` の後に `end_stream` が発火する)
- 発生条件はクライアントが CONNECT 直後に FIN を送るケースに限定される (高レベル `Client` もブラウザも CONNECT ストリームへ FIN を送出する手段を持たない)

## 設計方針

- 対応方法は実現可能性の調査を先に行う。候補は次の 2 つ:
  - `receive_stream_data` に渡る fin 引数と `pending_headers_` の残存から受理前 FIN を検知し、`accept_session` による受理と 2xx レスポンスの送信完了後に `close_stream` を呼ぶ遅延処理 (受理前の `close_stream` は `submit_wt_response` が NGHTTP3_ERR_STREAM_NOT_FOUND になり、クライアントがセッション確立を認識できなくなるため、受理後の遅延処理が必要)
  - `end_headers_cb` の fin 引数 (ヘッダーと FIN の同一読み取り時のみ fin が伝わる) を記録して受理後に処理する
- いずれも nghttp3 の状態機械との整合の確認が必要であり、まず調査してから方針を固める

## 完了条件

- 受理前 FIN (ヘッダーと FIN の同一読み取り) でも、セッション終了が検知されて `session_ids_` から削除され、`SessionClosed` イベントが発火する
- 通常のセッション確立 (FIN なし) は影響を受けない
- モックなしのテストで検証できる (Sans-IO 構成で `receive_stream_data` にヘッダーと FIN を同時に渡す)
