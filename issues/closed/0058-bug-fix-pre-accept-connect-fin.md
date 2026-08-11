# CONNECT ストリームの受理前 FIN でセッション終了が検知されない

- Created: 2026-08-10
- Completed: 2026-08-11
- Branch: feature/fix-pre-accept-connect-fin
- Polished: 2026-08-10

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」のうち、サーバーが応答を送信する前に CONNECT ストリームが FIN でクローズされた場合（受理前 FIN）にセッション終了の検知が成立しない問題を修正する。FIN 経路のセッション終了検知自体は実装済み (closed issue 0048) だが、受理前 FIN では nghttp3 の挙動により検知が成立せず、セッション ID が管理集合 `session_ids_` に残り続ける。

## 現状

- closed issue 0048 は受理前 FIN について「対応 (accept_session 後の遅延クローズ) は現設計の拡張では実装不能」と結論し、発生条件が異常・悪意系クライアントに限定されることから「許容」の判断とした。本 issue は検知経路を変えることでこの判断を再検討する (0048 が検討したのは `end_stream` コールバック経由の検知のみであり、本 issue の候補はその経路に依存しない)
- リクエストヘッダーと空 FIN が受理前 (サーバーが応答を送信する前) に処理される場合、nghttp3 はストリームを WT_SESSION_BLOCKED にして空 FIN を処理しないため、`end_stream` コールバックが発火しない。これはヘッダーと FIN が同一読み取りで到着した場合も、別の読み取りで届く場合も同じ (nghttp3 の read_bidi は WT_SESSION_BLOCKED 中に srclen == 0 なら早期 return し、ヘッダー処理後も「Server has not submitted response」の分岐で blocked を立てて早期 return する)
- `accept_session` によるセッション受理後も、process_blocked_wt_stream_data は inq が空 (空 FIN はバッファされない) のため `end_stream` は発火せず、FIN は喪失する。QUIC 層は fin を 1 回しか渡さないため、リトライで復元されることもない
- 結果として、セッションは確立される (SESSION_READY は発火し、`accept_session` は成功する) が、`SessionClosed` イベントは発火せず、セッション ID が `session_ids_` に残り続ける (接続終了まで)
- クライアント側の 200 レスポンスと FIN の同一読み取りは正常に検知できる (`end_headers_cb` の後に `end_stream` が発火する)
- 発生条件はクライアントが CONNECT 直後に FIN を送るケースに限定される (高レベル `Client` は CONNECT ストリームへ FIN を送出する手段を持たない。ブラウザの WebTransport API も CONNECT ストリームをアプリに露出しないため同様)

## 設計方針

- 対応方法は実現可能性の調査を先に行う。検知経路の候補は次の 2 つ:
  - `receive_stream_data` に渡る fin 引数による検知。サーバー側 (`is_server_`) の CONNECT ストリームに限定し、受信読み取り後に「fin が渡った ∧ 受け付け前に `end_stream` が発火しなかった (ストリームが `session_ids_` に含まれる ∧ `pending_fin_session_ids_` に含まれない)」で受理前 FIN と判定できる。判定は `nghttp3_conn_read_stream2` が `end_headers_cb` で `session_ids_` への挿入を完了した**後**に置く (前だと同一読み取りを検知できず、受理後 FIN を誤検知して二重処理し得る)。判定したセッション ID は保留集合に記録し、`accept_session` による受理と 2xx レスポンスの送信完了後に `close_stream(session_id, 0)` を呼ぶ遅延処理で後始末する (受理前の `close_stream` は `submit_wt_response` が NGHTTP3_ERR_STREAM_NOT_FOUND になり、クライアントがセッション確立を認識できなくなるため、受理後の遅延処理が必要)。遅延クローズの実行場所 (get_streams_to_send 内 / accept_session 内のフラグ管理 / 新設メソッド) と、既存の `pending_fin_session_ids_` (毎回 `receive_stream_data` 末尾で clear される) とは別の専用保留集合の設計は調査対象とする。`close_stream` は未送信の 200 レスポンスを破棄し得るため、200 が `get_streams_to_send` で QUIC 層へ書き出されたことを確認してから呼ぶ。ただし未送信ストリームの空判定は「書き出し済み」と誤判定し得るため、受理完了と 200 書き出しの 2 条件を満たしたことを確認しないと受理前に close_stream が走って STREAM_NOT_FOUND を再現する。受理されない場合 (高レベル `reject_session` 経路) の保留エントリの処置は調査対象とし、現状の `session_ids_` 残留と同じ扱いを想定する
- `end_headers_cb` の fin 引数による検知。`end_headers_cb` の fin 引数はヘッダーと FIN の同一読み取り時のみ伝わる (nghttp3 は `p == end && fin` で渡す)。現行実装は `(void)fin` で破棄しており、これを記録して受理後に処理する。この経路は別読み取りで届く受理前 FIN を検知できないため、完了条件 (別読み取りを含む) を満たせるのは候補 1 のみであり、候補 2 は候補 1 の代替 (同一読み取りのみが対象なら候補 2 単独でも可)。検知はサーバー側 (`is_server_`) の CONNECT ストリームに限定する (クライアント側では 200 レスポンスと FIN の同一読み取り時に fin が渡り得るが、受理後 FIN として 0048 の `end_stream` 経路が処理済みのため対象外)
- いずれの候補も、nghttp3 の状態機械との整合と、既存の受理後 FIN 経路 (0048 の `pending_fin_session_ids_` 処理) との二重処理回避 (判定順序・冪等性) の確認が必要
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (受理前 FIN の検知と遅延クローズ)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 受理前 FIN (ヘッダーと FIN の同一読み取り、およびヘッダー後に別読み取りで届く受理前 FIN の両方) でも、セッション終了が検知されて `session_ids_` から削除され、`SessionClosed` イベントが発火する
- 受理前 FIN 経路の `SessionClosed` イベントの `error_code` は 0 であること (0048 の FIN 経路と同じ意味論。WT_CLOSE_SESSION 無しのクリーンクローズは error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION と等価。draft-ietf-webtrans-http3-16 Section 6)
- 通常のセッション確立 (FIN なし) は影響を受けない
- 既存の受理後 FIN 経路 (0048 の実装) は影響を受けず、`SessionClosed` が二重に発火しない
- モックなしのテストで検証できる (Sans-IO 構成で `receive_stream_data` にヘッダーと FIN を同時に渡すケースと、別々に渡すケース)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` に受理前 FIN の検知を追加した。`receive_stream_data` に渡る fin 引数と、サーバー側 (`is_server_`) の CONNECT ストリーム判定 (`session_ids_` のメンバーシップ)、`pending_fin_session_ids_` に未記録 (受理後 FIN の除外) の組み合わせで受理前 FIN を検知し、新メンバー `pending_pre_accept_fin_session_ids_` に記録する。判定は `nghttp3_conn_read_stream2` から戻った後 (`end_headers_cb` による `session_ids_` 挿入完了後) かつ `pending_fin_session_ids_` の clear 前に置き、同一読み取り (ヘッダー + FIN) と別読み取りの両方を検知できる
- `accept_session` で受理した検知済みセッションを `pre_accept_fin_accepted_session_ids_` に移し、`get_streams_to_send` の書き出しループ後で `nghttp3_conn_is_stream_flushed` による 2xx レスポンスの書き出し完了を確認してから `close_stream(session_id, 0)` で遅延クローズする (未送信の 2xx を破棄せず、クライアントがセッション確立を認識できる)
- 検知後は終了を学習した状態であるため、`send_datagram` / `open_stream` に受理前 FIN 検知済みセッションの拒否を追加した (draft-ietf-webtrans-http3-16 Section 6 の MUST「新しいデータグラムを送信せず、新しいストリームも開かない」を close_stream までの窓でも満たす)
- `src/bindings/webtransport_h3.h` に新メンバー 2 つと `accept_session` / `send_datagram` の docstring 更新を追加した (受理前 FIN の自動終了処理と保留条件、第 4 の拒否経路)
- テスト `tests/test_webtransport_h3_pre_accept_fin.py` を新規作成した (9 件)。同一読み取り・別読み取りの検知、error_code 0、通常確立の非影響、受理後 FIN の二重処理なし、reject_session 経路の残留、複数セッション時の独立、検知後の送信拒否、遅延クローズの保留 (block_stream)、保留中の WT_CLOSE_SESSION 受信を Sans-IO 構成で検証する
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
