# 終了したセッション宛のデータストリーム受信で nghttp3 がセッションを復活させる

- Created: 2026-08-10
- Completed: 2026-08-10
- Branch: feature/fix-late-stream-revives-session
- Polished: {YYYY-MM-DD}

## 目的

セッション終了 (CONNECT ストリームのクローズ) 後に、そのセッション ID 宛のデータストリームを仕様逸脱ピアが開いて送信すると、nghttp3 がサーバー側で CONNECT ストリームを新規作成してセッションを「復活」させ、データを接続終了まで保持する問題を修正する。セッション終了の検知経路が整備されたことで、このシナリオが実際に到達可能になった。

## 現状

- nghttp3 の `nghttp3_conn_on_wt_stream` は、サーバー側でセッション ID の CONNECT ストリームが存在しない場合、`nghttp3_conn_create_stream` で CONNECT ストリームを新規作成し、`nghttp3_conn_open_wt_session` でセッションを開く (クライアント側は NGHTTP3_ERR_WT_SESSION_GONE を返すため復活しない)
- セッション終了後に仕様逸脱ピアがそのセッション ID 宛のデータストリームを開いてデータを送信すると、`recv_wt_data_cb` は呼ばれず、データは nghttp3 の inq に永続バッファされる (接続終了まで保持。メモリが無制限に増加し得る)
- `H3Session` はこの復活を検知できない (WT データストリームの受信時にセッション ID を検証する経路が存在しない)

## 設計方針

- 実現可能性の調査を先に行う。候補は次の 2 つ:
  - WT データストリームの受信時に、ヘッダーから復元したセッション ID が `session_ids_` に存在しない場合にストリームを破棄する
  - nghttp3 に「閉じたセッション」を通知する手段 (存在すれば) を使う
- いずれも nghttp3 の内部挙動に依存するため、調査してから方針を固める

## 完了条件

- 終了したセッション ID 宛のデータストリーム受信でセッションが復活せず、データが破棄される
- 生存セッションのデータストリーム受信は影響を受けない
- モックなしのテストで検証できる

## 解決方法

対応不要 (closed) と判断した。`/polish-issue` の必要性判断と反対尋問で、本 issue の前提が反証されたため。

- nghttp3 の `nghttp3_conn_on_wt_stream` (`_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` の `nghttp3_conn_on_wt_stream`) は、CONNECT ストリーム新規作成の前に `conn_bidi_idtr_open` でセッション ID の idtr チェックを行う。確立済みセッションの ID は CONNECT リクエスト受信時に idtr へ push 済みであり (`nghttp3_conn_read_stream2` の `conn_bidi_idtr_open`)、idtr は一度 push した ID を解放しない (`nghttp3_idtr.c` の `nghttp3_idtr_open` / `nghttp3_gaptr.c` の `nghttp3_gaptr_is_pushed`)。そのためセッション終了後に `nghttp3_idtr_open` は `NGHTTP3_ERR_STREAM_IN_USE` を返し、`nghttp3_conn_on_wt_stream` は `NGHTTP3_ERR_WT_SESSION_GONE` を返してデータストリームを WT_SESSION_GONE で破棄する
- Sans-IO の実機再現で、FIN / CONNECT リセットで終了したセッション ID 宛の late データストリームは WT_SESSION_GONE (0x170D7B68) で破棄され、セッションの復活は発生しないことを確認した。本 issue の完了条件「終了したセッション ID 宛のデータストリーム受信でセッションが復活せず、データが破棄される」は現実装で既に満たされている
- 一方、本 issue の調査で **close_session (WT_CLOSE_SESSION 送出) / WT_CLOSE_SESSION 受信経路では nghttp3 の CONNECT ストリームが残存するため、終了したセッション ID 宛のデータストリームが `recv_wt_data_cb` 経由でアプリに配送される (ghost 配信)** という実在する別問題が判明した。これは本 issue の記述シナリオ（CONNECT ストリームのクローズ）とはトリガーが異なるため、別 issue として起票する (open issue 0059)
