# QPACK デコードブロック中の受理前 FIN が検知されない

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-qpack-blocked-pre-accept-fin-loss
- Polished: 2026-08-12

## 目的

QPACK デコードブロック中に届いた受理前 FIN (サーバーが応答を送信する前に CONNECT ストリームが FIN で閉じられた) の fin が喪失し、以後どの経路でもセッション終了が検知されない問題を修正する。0058 で対応した fin 引数による検知は、ヘッダーが fin 到着時点でデコード済みの場合に限定される。

## 現状

- 0058 の検知条件は `session_ids_.count(stream_id) > 0` (ヘッダー処理完了 = `end_headers_cb` 実行済み) に依存する。ヘッダーが QPACK デコードブロック中に fin 付きデータが届くと、ヘッダー未処理のため検知が成立しない
- nghttp3 の挙動 (実測確認済み):
  - ブロック中のデータは `nghttp3_stream_buffer_data` で inq にバッファされ、ブロック解除後にヘッダーは正しくデコードされ、`session_ids_` に挿入されて SESSION_READY は発火する (ブロック中に届くのがヘッダーのみの場合。ブロック解除時に inq にヘッダー以外のデータ (DATA フレーム等) が存在すると、同一読み取り・別読み取りを問わず、ブロック解除の再処理で nghttp3 側の異常挙動 (クラッシュ / 無限ループ) が発生する既知の制約があるため、本 issue のテスト構成はヘッダーのみを対象とする)
  - 空 FIN (srclen == 0) はバッファされず `NGHTTP3_STREAM_FLAG_READ_EOF` として保存される。ブロック解除後の `process_blocked_stream_data` は inq の最後のチャンクに READ_EOF を fin として伝播する (`len == 1 && READ_EOF`) ため fin 自体は `read_bidi` に渡るが、`read_bidi` がヘッダー完了後に「Server has not submitted response」の分岐で WT_SESSION_BLOCKED を立てて早期 return するため、`almost_done` の fin 処理 (`end_stream` コールバック) に到達せず fin は喪失する
  - 結果として fin は喪失し、既存の 2 経路 (`receive_stream_data` の fin 引数検知・`end_stream` コールバック) では検知されない。セッションは確立されるが終了検知されず、`session_ids_` に残り続ける (接続終了まで)
  - なお、ブロック解除後の `end_headers_cb` は fin 引数 1 で呼ばれ得る (`conn_call_end_headers` の `p == end && fin`。ヘッダーが READ_EOF の伝播する inq 最後のチャンクの末尾で完了する場合。後続データがある場合やチャンクの途中で終わる場合は 0 のまま)
- 発生条件は QPACK エンコーダーストリームとリクエストストリームのストリーム間の到着順序の入れ替わり (パケットロスに伴う再送遅延等) で発生し得る

## 設計方針

- 検知経路: `receive_stream_data` に渡る fin 引数で「fin が渡ったが `session_ids_` に未挿入 (ヘッダー未処理) のストリーム」を保留集合に一時記録し、後で `end_headers_cb` が CONNECT 判定 (`session_ids_` への挿入) を行った時点で受理前 FIN とみなす。判定は `nghttp3_conn_read_stream2` から戻った後 (`end_headers_cb` による `session_ids_` 挿入・`pending_headers_` 削除の後) に置くこと。これが、0058 の既存検知 (`session_ids_.count > 0`) との排他性の前提になる (read_stream2 の前に判定すると、QPACK ブロックなしの同一読み取り (ヘッダー + FIN) でも保留記録され排他が崩れる)
- 記録条件は「`fin` かつサーバー側 (`is_server_`) かつ `session_ids_.count == 0` かつ `pending_headers_` に含まれる」とする。`pending_headers_` のメンバーシップ (`begin_headers_cb` 発火済み・`end_headers_cb` 未発火でエントリが残る) により、CONNECT 以外のストリーム (WT データストリーム・ヘッダー処理済みの通常リクエスト・制御ストリーム等) の FIN を記録から除外する。`end_headers_cb` で CONNECT 判定されなかったストリーム (非 CONNECT リクエスト・Origin 検証失敗の reject 経路・ヘッダーフレーム未完のまま fin が届いてプロトコルエラーになる経路) の保留エントリは、`end_headers_cb` の該当分岐またはエラー処理で除去する (除去できない場合は接続終了まで残留する既知の制約)
- `end_headers_cb` の fin 引数による検知 (0058 の設計候補 2) は不採用とする: fin 引数が 1 になるのはヘッダーが READ_EOF の伝播する inq 最後のチャンクの末尾で完了する場合に限定され (後続データがある場合やチャンクの途中で終わる場合は 0)、読み取り構成に依存して不完全なため
- 判定したセッション ID は既存の `pending_pre_accept_fin_session_ids_` へ移し、0058 と同じ遅延クローズ (`accept_session` 受理 + 2xx 書き出し完了後に `close_stream`) を流用する。0058 の既存検知 (fin かつ `session_ids_.count > 0`) との二重検知は、判定条件が排他 (`count == 0` と `> 0`。判定位置を read_stream2 の後に置くことが前提) であり、保留集合が `std::set` で冪等なため発生しない。なお、`accept_session` の移行処理 (webtransport_h3.cpp の `accept_session`) は「その時点で `pending_pre_accept_fin_session_ids_` に含まれるか」で判定するため、SessionReady 発火前に `accept_session` が呼ばれた場合 (低レベル API で可能) は移行が後からでも機能するよう、`end_headers_cb` での移行時に accept_session 済みかどうかを確認して `pre_accept_fin_accepted_session_ids_` へ直接移す等の対応が必要
- 実現可能性の調査は実装時に必要 (nghttp3 の状態機械との整合、`end_headers_cb` と保留集合のタイミング)。調査で不可能と判明した場合は代替経路を再検討する
- 遅延クローズ機構 (`pre_accept_fin_accepted_session_ids_` と `get_streams_to_send` の `stream_flushed` 確認) は issue 0065 (遅延クローズ保留中の未送信 2xx 送出) の修正対象と同一である。本 issue の実装は検知経路の追加に留め、0065 の対応内容 (保留集合の清掃・未送信 2xx の扱い) は本 issue に含めない
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (検知と保留集合、`receive_stream_data` の既存コメント (「既知の制約」記述) の更新)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- QPACK デコードブロック中の受理前 FIN でも、セッション終了が検知されて `session_ids_` から削除され、SessionClosed イベント (error_code 0) が発火する
- 通常の受理前 FIN (0058 の対応済み経路) は影響を受けず、SessionClosed が二重に発火しない
- 通常のセッション確立 (FIN なし) は影響を受けない
- モックなしの Sans-IO テストで検証できる (QPACK エンコーダーストリームの到着を遅延させ、ブロック中に SESSION_READY が発火しないことを確認したうえで、同一読み取り (ヘッダー + FIN) と別読み取り (ヘッダー → 空 FIN) の両方を検証する。ヘッダー後続データを含む読み取りは nghttp3 側の既知の異常挙動 (クラッシュ / 無限ループ) があるため対象外とする)
