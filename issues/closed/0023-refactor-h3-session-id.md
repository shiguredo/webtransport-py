# H3Session のセッション ID 管理を nghttp3 API に置き換える

- Created: 2026-08-04
- Completed: 2026-08-04
- Branch: feature/refactor-h3-session-id
- Polished: {YYYY-MM-DD}

## 目的

`H3Session` が `stream_info_` で自前管理しているセッション ID 対応を、nghttp3 の `nghttp3_conn_get_stream_wt_session_id` に置き換える。自前管理の重複を解消し、0017 で公開する `stream_wt_session_id` の実装と整合させる。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session` は `stream_info_` (`StreamInfo.session_id`) でストリームとセッション ID の対応を自前管理している (`send_stream_data` の書き込み登録・`close_session` の走査・各コールバックのイベント補完に使用)
- nghttp3 には `nghttp3_conn_get_stream_wt_session_id` が存在し、WT データストリームのセッション ID を返す (WT データストリーム以外は -1)

## 設計方針

- `nghttp3_conn_get_stream_wt_session_id` は WT データストリームのみセッション ID を返すため、置き換え対象は WT データストリームのセッション ID 取得に限定する
- CONNECT ストリームのフォールバック (0009 の `session_ids_` メンバーシップ判定) は置き換え対象外とする (get_stream_wt_session_id は CONNECT ストリームに -1 を返すため)
- 0009 / 0010 が `stream_info_` を変更対象とするため、実装順序によるマージの競合に注意する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (stream_info_ のセッション ID 参照箇所の置き換え)

## 完了条件

- WT データストリームのセッション ID 取得が `nghttp3_conn_get_stream_wt_session_id` ベースになる
- 既存の e2e テスト (0009 の 2 セッション構成・0010 のバッファ削除) が引き続き通る

## 解決方法

polish-issue の必要性判断 (反対尋問込み、確信度: 高) で「不要」と判定され、実装しないことになった。

- 目的 (自前管理の重複を解消) が構造的に達成不能: `stream_info_` の session_id フィールドは逆引き (`close_session` / `get_session_streams` / `recv_wt_close_session_cb` の走査)・書き込み (`open_stream` / `send_stream_data` / `recv_wt_data_cb` の登録)・フォールバック (`send_stream_data` の `session_ids_.begin()`) のために残存必須であり、`nghttp3_conn_get_stream_wt_session_id` (順引きのみ) では代替できない
- 置き換え可能な残余箇所 (`stop_sending_cb` / `reset_stream_cb` / `stream_close_cb` のイベント補完) は、CONNECT ストリームのリセット経路 (`conn_unlink_wt_session` が `wt.session = NULL` 後にコールバックを同期発火する) でセッション ID が -1 に劣化し、0010 の設計前提 (CONNECT リセット経路で `stream_info_` からセッション ID を取得できること) と矛盾する
- 実質的に置き換え可能なのは `recv_data_cb` (WT データストリームでは発火しないデッドコード) 程度で、リファクタリングの価値が極めて薄い

代わりに、0017 の実装時に「内部コールバックからの nghttp3 API 再利用は安全な箇所 (CONNECT ティアダウン経路を除く) に限る」旨を 0017 の設計方針に追記して統合するのが適切と判断した。
