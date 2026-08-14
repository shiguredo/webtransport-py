# 受理前の WT_CLOSE_SESSION が accept_session 中に処理されて SessionClosed が二重発火する

- Created: 2026-08-12
- Completed: 2026-08-14
- Branch: feature/fix-pre-accept-wt-close-session-double-close
- Polished: 2026-08-12

## 目的

クライアントが受理前 (サーバー応答前) に送った WT_CLOSE_SESSION カプセルが、`accept_session` の処理中にバッファから処理され、SessionClosed イベントが二重発火する・終了済みセッションの CONNECT ストリームに未送信の 2xx が送出される・`session_ids_` にセッション ID が残留する問題を修正する。受理前のカプセル送信は楽観的送信として許容され (draft-ietf-webtrans-http3-16 Section 3.2 の「A client MAY optimistically send capsules on the CONNECT stream before receiving the server's response. A server MUST NOT process these bytes as capsules until it sends a 2xx response accepting the session.」)、サーバーは 2xx 送出後にバッファを処理するため `accept_session` 中に同期処理される。0065 の実装時にスコープ外として切り出した。サーバー側の `reject_session` 経路 (0068) とは独立の経路である。

## 現状

- `src/bindings/webtransport_h3.cpp` の `accept_session` は `nghttp3_conn_server_confirm_wt_session` を呼んだ後 (成功時) に `session_ids_` への挿入と、`pending_pre_accept_fin_session_ids_` から `pre_accept_fin_accepted_session_ids_` への移行を行う
- 受理前に送られた WT_CLOSE_SESSION カプセルは nghttp3 の inq にバッファされ、`nghttp3_conn_server_confirm_wt_session` → `nghttp3_conn_on_wt_session_confirmed` → `nghttp3_conn_process_blocked_wt_stream_data` の経路で **accept_session の処理中に同期処理され、`recv_wt_close_session_cb` が発火する** (nghttp3 の実装順序に依存するため、Sans-IO テストで経路をピン留めする)
- このときの実測挙動 (Sans-IO 構成で確認済み):
  - `recv_wt_close_session_cb` の `session_ids_.erase` は有効に実行される (ID は受理前に `end_headers_cb` で挿入済み) が、confirm から戻った後に `accept_session` 自身の `session_ids_.insert` が再挿入するため、セッション ID が残留する
  - 0065 の破棄処理の保留記録条件 (`pre_accept_fin_accepted_session_ids_` への挿入が confirm の後) を満たさないため、未送信の 2xx は破棄されず送出される
  - 受理前 FIN を伴う場合 (準拠クライアントは WT_CLOSE_SESSION 直後に FIN を送る。Section 6 の MUST)、移行済みセッションの `get_streams_to_send` の遅延クローズループが残留したセッション ID で `close_stream` を実行し、SessionClosed が 2 回目に発火する
  - 受理前 FIN を伴わない場合 (WT_CLOSE_SESSION のみ受理前到着)、残留した ID 宛の `send_datagram` / `open_stream` が成功し得る (draft-ietf-webtrans-http3-16 Section 6 の MUST「it MUST NOT send any new datagrams or open any new streams」に反する窓)

## 設計方針

- `accept_session` の移行処理 (`pre_accept_fin_accepted_session_ids_` への挿入) を `nghttp3_conn_server_confirm_wt_session` の前に移動し、recv_wt_close_session_cb の発火時点で移行済みにする (0065 の破棄処理の保留記録条件が成立するようになる)。移動位置は `nghttp3_conn_submit_wt_response` の成功後・confirm の前とし、submit 失敗時は移行しない。confirm 失敗時 (accept_session が false を返す場合) は、移行済みエントリを `pending_pre_accept_fin_session_ids_` に戻す (除去すると終了学習済みセッションへの送信ブロックが解除され、Section 6 の MUST に反する窓が開くため、戻す方に一本化する)。既存コメント「confirm_wt_session が失敗して false を返す場合は検知エントリが pending_pre_accept_fin_session_ids_ に残る」の意味論が変わるため、コメント更新が必要 (confirm 失敗後にアプリが `reject_session` を呼ぶ流れと、0068 の「reject_session で pending_pre_accept_fin_session_ids_ のエントリも除去する」設計の相互関係も実装時に確認する)
- `accept_session` が confirm 後に `session_ids_` へ再挿入する処理を抑止する: サーバー側の挿入は `end_headers_cb` が受理前に行っており再挿入は冗長であり、再挿入があると recv_wt_close_session_cb の erase が無効化されて、残留・二重発火・送信窓がすべて残る。実装方法は (a) insert の削除、(b) confirm 後に `session_ids_` に含まれる場合のみ挿入 (recv_wt_close_session_cb が erase したセッションは含まれないため再挿入されない)、(c) confirm 前への移動 のいずれかでよい
- 破棄処理の実行タイミング: 0065 の破棄処理 (`pending_stale_2xx_discard_session_ids_` の処理) は `receive_stream_data` の read_stream2 後段にしか存在しないため、accept_session 直後に呼ばれる `get_streams_to_send` が 2xx を先に書き出す。accept_session はアプリ呼び出しであり nghttp3 コールバック内ではないため、confirm から戻った後 (confirm 成功時のみ) に 0065 と同じ破棄処理を accept_session 内でも実行する: `pre_accept_fin_accepted_session_ids_` から先に除去してから close_stream する (0065 と同じ防衛。除去しないと、存在しないストリームは `stream_flushed` が 1 を返すため遅延クローズループが 2 回目の close_stream を実行し得る)。処理したエントリは `pending_stale_2xx_discard_session_ids_` からも除去し、receive_stream_data の後段で二重に close_stream しないようにする (再挿入抑止により `session_ids_` に含まれないため、close_stream の CONNECT ストリーム判定が成立せず SessionClosed は再発火しない。STREAM_CLOSED イベントは積まれる)。破棄処理は 0065 と同じ保留集合の全走査でよい (他セッションの保留エントリも破棄対象であり、全走査で破棄されるのが正しい)
- 破棄記録条件を拡大する: カプセルと FIN が別の受信イベント (別 QUIC パケット) で届くと、FIN 検知前に accept_session が実行され、`pre_accept_fin_accepted_session_ids_` のメンバーシップが成立しない (準拠クライアントでも発生し得る)。recv_wt_close_session_cb の破棄記録条件を「`pre_accept_fin_accepted_session_ids_` に含まれる」に加えて「accept_session の confirm 処理中に発火した」場合も対象に拡大する (accept_session 処理中フラグの追加等の実装方法でよい)。confirm 中に発火した recv_wt_close_session_cb は受理前バッファの WT_CLOSE_SESSION の処理であり、2xx が submit 済みのため破棄対象になる。confirm が失敗する場合は process_blocked_wt_stream_data が呼ばれず recv_wt_close_session_cb も発火しないため、実際には「発火済み + confirm 失敗」は発生しない (前提が崩れた場合の処理 (記録済みエントリの残留等) は実装時に確認する)。accept_session 内の破棄処理の close_stream が FIN 到着前に実行されるため、後続の空 FIN の `receive_stream_data` が閉じられたストリームに対してエラーを返し Error イベントが積まれる可能性がある (実サーバーでは無視される。Sans-IO テストの別イベント変種で観測され得るため確認する)
- 受理前 FIN を伴わない変種 (WT_CLOSE_SESSION のみ受理前到着) は、再挿入抑止により ID が残留せず `send_datagram` / `open_stream` の窓も塞がる (移行処理は FIN なしでは機能しないが、残留自体が解消されるため)。この変種の 2xx は破棄記録条件の拡大により破棄される
- 0064 が「accept_session 内の既存の移行処理がそのまま機能する」ことを前提に実装した経路 (QPACK ブロック中の受理前 FIN) は、移行処理の移動後も receive_stream_data 内で完結しており影響を受けない (実装時に整合を確認する)。QPACK ブロック中に WT_CLOSE_SESSION カプセルが混在する読み取りは nghttp3 側の既知の異常挙動 (receive_stream_data のコメント) の対象であり、本 issue の対象外
- カプセルが confirm 時点で未到着の場合は受理後の通常の WT_CLOSE_SESSION 受信経路になり、本 issue の対象外 (既存の挙動のまま)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`accept_session` の移行順序・再挿入抑止・破棄処理の実行・破棄記録条件の拡大と、`accept_session` / `recv_wt_close_session_cb` のコメント更新。「2xx 送出と SessionClosed の二重発火は別途の検討対象」という 0065 由来の記述を解消する)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 受理前の WT_CLOSE_SESSION が accept_session 中に処理されても、SessionClosed が 1 回だけ発火する
- 終了済みセッションの未送信 2xx が送出されない (受理前 FIN の有無・FIN の到着タイミング (カプセルと同一イベントか別イベントか) にかかわらず、破棄処理が機能する)
- `session_ids_` にセッション ID が残留せず、`send_datagram` / `open_stream` が終了を学習済みのセッション ID で成功しない (受理前 FIN の有無にかかわらず)
- 通常の受理前 FIN の遅延クローズ (accept_session → 2xx 書き出し完了後に close_stream) は影響を受けない
- モックなしの Sans-IO テストで検証できる (クライアントが受理前に WT_CLOSE_SESSION を送出する構成。Section 6 の MUST により準拠クライアントは FIN を伴うため、FIN ありの準拠シナリオで構成する (FIN なし変種の窓閉塞・破棄もテストで確認する)。サーバーが accept_session した後に、SessionClosed が 1 回だけ発火すること・STREAM_CLOSED が 1 回だけ発火すること (accept_session 内破棄と receive_stream_data 後段の二重 close_stream の回帰検出)・`get_session_ids()` が空であること・`get_streams_to_send()` の戻り値に 2xx が現れないこと (h3 層の戻り値で判定する)・`send_datagram` / `open_stream` が拒否されることを確認する。カプセルと FIN が別イベントで届く変種 (カプセルのみを先に注入し、accept_session 後に空 FIN を渡す構成) も破棄記録条件の拡大の検証としてテストに含める。テストは tests/test_webtransport_h3_pre_accept_fin.py への追加が自然)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `accept_session` で、受理前 FIN の移行処理 (`pending_pre_accept_fin_session_ids_` → `pre_accept_fin_accepted_session_ids_`) を `nghttp3_conn_server_confirm_wt_session` の前に移動した。confirm の処理中 (process_blocked_wt_stream_data) に発火する `recv_wt_close_session_cb` の時点で移行済みになり、0065 の破棄記録条件 (未送信 2xx の破棄) が成立する。confirm 失敗時は移行済みエントリを `pending_pre_accept_fin_session_ids_` に戻す (除去すると終了学習済みセッションへの送信ブロックが解除され、draft-ietf-webtrans-http3-16 Section 6 の MUST に反する窓が開くため)
- confirm 後に `session_ids_` へ再挿入していた `insert` を削除した (サーバー側の挿入は `end_headers_cb` が受理前に行っており再挿入は冗長。再挿入があると `recv_wt_close_session_cb` の erase が無効化され、セッション ID 残留・SessionClosed 二重発火・送信窓がすべて残る)
- 0065 の破棄処理 (`pending_stale_2xx_discard_session_ids_` の処理) を `discard_stale_2xx()` ヘルパーに抽出し、`receive_stream_data` の後段に加えて `accept_session` 内 (confirm 成功後と失敗後の両方) でも実行するようにした。`accept_session` 直後に呼ばれる `get_streams_to_send` が 2xx を先に書き出すのを防ぐ
- `recv_wt_close_session_cb` の破棄記録条件を「`pre_accept_fin_accepted_session_ids_` に含まれる」に加えて「accept_session の confirm 処理中に発火した (`accepting_session_id_` と一致する)」に拡大した。カプセルと FIN が別の受信イベントで届いた場合 (FIN 検知前に accept_session が実行される) は移行処理が成立しないため、この条件が無いと破棄されない
- `src/bindings/webtransport_h3.h` に `accepting_session_id_` メンバーを追加し、`accept_session` / `recv_wt_close_session_cb` / `discard_stale_2xx` のコメントと docstring を更新した (0065 由来の「2xx 送出と SessionClosed の二重発火は別途の検討対象」の記述を解消)
- `tests/test_webtransport_h3_pre_accept_fin.py` に 4 件追加した: `test_pre_accept_wt_close_session_fin_same_read` (FIN と同一読み取り = 準拠シナリオ)、`test_pre_accept_wt_close_session_fin_late` (FIN が別読み取りで遅れて届く変種。破棄記録条件の拡大の検証)、`test_pre_accept_wt_close_session_no_fin` (FIN なし変種の窓閉塞)、`test_pre_accept_wt_close_session_other_session_unaffected` (他セッションの生存への波及なし)
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
