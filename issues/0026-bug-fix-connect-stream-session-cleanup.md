# CONNECT ストリームのリセット時にセッション終了の後始末が行われないのを修正する

- Created: 2026-08-04
- Completed: 2026-08-08
- Branch: feature/fix-connect-stream-session-cleanup
- Polished: 2026-08-08

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」のうち、CONNECT ストリームのリセット (abrupt) でセッションが終了しても、セッション ID が管理集合 `session_ids_` に残り続け、アプリケーションへの終了通知 (`on_session_closed`) が発火しない問題を修正する。クリーンクローズ (FIN) 経路の検知は本 issue の対象外とし、open issue 0048 で対応する (H3Session は end_stream コールバックを登録しておらず、検知経路の追加は別途の実装単位になるため)。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` は CONNECT ストリームのリセット時に `session_ids_` から削除しない (削除は `close_session` / `recv_wt_close_session_cb` のみ)
- `H3Session::recv_wt_close_session_cb` は WT_CLOSE_SESSION カプセル専用のため、CONNECT ストリームのリセットでは呼ばれない。`stream_close_cb` も CONNECT ストリームのセッション ID を復元できない (CONNECT ストリームは `stream_info_` に登録されない)
- そのため、CONNECT ストリームのリセットでセッションが終了しても `SessionClosed` イベントが生成されず、高レベル `Server` の `on_session_closed` は呼ばれない (高レベル `Client` も同様。`src/webtransport/h3/client.py` の SESSION_CLOSED ハンドラが `_connected = False` にする)
- 死んだセッション ID が `session_ids_` に残り続けるため、`get_session_ids()` が不正確になり、`H3Session::send_stream_data` のフォールバック (`src/bindings/webtransport_h3.cpp` の `send_stream_data`。`session_ids_` の先頭要素に依存) が死んだセッションを選び得る。このフォールバックの解消自体は open issue 0027 の管轄であり、本 issue は死んだセッションの除去 (セッション ID の削除) までを担当する
- 0009 の CONNECT ストリームのフォールバック (close_stream の戻り値復元) は「CONNECT ストリームのリセット時に `session_ids_` から削除されない挙動」に依存している。本 issue で削除を追加する際は、戻り値の復元を削除より先に行って 1 回目のリセットの戻り値を維持する (設計方針参照)
- セッション終了時にセッションに属するストリームを WT_SESSION_GONE で破棄する Section 6 の MUST は、`nghttp3_conn_close_stream` を CONNECT ストリームに対して呼んだ時点で nghttp3 内部が実施する (セッションに属するデータストリームへ stop_sending_cb / reset_stream_cb を WT_SESSION_GONE で発火し、高レベル層が QUIC の STOP_SENDING / RESET_STREAM として送出する)。そのため本 issue での対応は不要

## 設計方針

- CONNECT ストリームのリセット (close_stream 経路) でセッション終了を検知し、`session_ids_` から削除して `SessionClosed` イベントを発火する。`SessionClosed` イベントには `close_stream` に渡された `error_code` (QUIC STREAM_RESET のエラーコード) を載せ、`error_message` は空とする
- `close_stream` の戻り値 (CONNECT ストリームの場合はセッション ID) の復元は 0009 の実装どおり `session_ids_` のメンバーシップ判定で行う。`session_ids_` からの削除を戻り値の復元より後に置くことで、1 回目のリセットでは引き続きセッション ID が返り、0009 の既存テスト (`test_stream_reset_connect_stream_session_id`) が維持される。2 回目以降の同一 CONNECT ストリームのリセットでは `session_ids_` から削除済みのため -1 が返る (セッションは既に終了しているため許容。データストリームの二重リセットの -1 と対称)
- セッションに属するデータストリームの `stream_info_` エントリの清掃を後始末の一部として行う (0010 が「エントリの清掃はセッション終了の後始末と合わせて別途検討する」と先送りした項目)。既存ヘルパー `erase_session_streams` 相当の処理で残存エントリを削除する (`stream_close_cb` が同期コールバックで削除済みのエントリを含む)。削除のタイミングは `nghttp3_conn_close_stream` の同期コールバック (reset_stream_cb / stop_sending_cb / stream_close_cb) の後とし、コールバック内のセッション ID 復元 (stream_info_ の残存に依存) を壊さない。0010 の `test_connect_stream_reset_releases_session_send_buffers` が引き続き通ることを確認する。清掃により、セッション終了後に発火するイベントの `session_id` は -1 になる (セッション終了済みのため許容): ピア応答でデータストリームの `stream_close_cb` が発火した場合と、ピアが送るデータストリームの RESET_STREAM への `close_stream` 応答 (`on_stream_reset` に -1 が渡る。0009 の契約からの変化) が該当する
- 上記の清掃により、死んだセッションのストリームへの事後 `send_stream_data` は `stream_info_` の残存に頼れなくなり、フォールバック (`session_ids_` の先頭要素) が生存セッションを選んで誤ったセッションへデータが配送される可能性が生じる (清掃前は `open_wt_data_stream` が失敗してバッファに留まるだけだった)。フォールバックの解消は open issue 0027 の管轄。本 issue のテストはこの誤配送経路を検出しないため、実装順序は 0027 を先にするのが望ましい (0026 先行の場合は誤配送が 0027 の実装まで残る)
- クリーンクローズ (FIN) 経路の検知は対象外 (open issue 0048 で対応)
- 高レベル `Server` の SESSION_CLOSED ハンドラ (`src/webtransport/h3/server.py`) は CONNECT リセット由来の SessionClosed でも呼ばれる。ハンドラが CONNECT ストリームへ空 FIN を送出する挙動 (WT_CLOSE_SESSION 受信を想定した設計) がリセット経路で問題にならないことをテストで確認する (空 FIN は QUIC 層へ直接送出される。対向からのリセット経路では送信側は開いたままのため送出は成功し、対向の h3 層でクローズ済み CONNECT ストリームへの無害なエラーイベントを生じ得る。自側がリセットした場合は送出が失敗し得るが、いずれも許容する。該当テストが無ければ完了条件のテストで確認する)
- `end_headers_cb` は `session_ids_` への挿入と SessionReady の発火を同一コールバック内で行うため、受理前に CONNECT ストリームがリセットされた場合は `on_session_ready` (受理処理) と `on_session_closed` が連続して発火し、`accept_session` は失敗し得る (この挙動は許容する)。ヘッダー受信より先にリセットされたストリームは `session_ids_` に未登録のため、`SessionClosed` は発火しない
- CONNECT ストリームのリセット時、高レベル `Server` では `_process_quic_events` の STREAM_RESET ハンドラが先に `on_stream_reset` (セッション ID 付き) を発火し、続く `_process_webtransport_events` で `on_session_closed` が発火する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (セッション終了の検知と後始末。`webtransport_h3.h` の `close_stream` のドキュメントコメントと、`webtransport_h3.cpp` の `close_stream` 内の stale コメント (「session_ids_ からも削除しない」「エントリの清掃は別途検討する」) を更新)、テスト (`tests/test_e2e_webtransport_h3.py` に完了条件のテストを追加。0009 の低レベル API クライアント構成を流用する。`tests/test_webtransport_h3_stream_buffer_cleanup.py` は stale コメントの更新)。`src/webtransport/h3/server.py` の変更は想定しない (既存ハンドラの挙動をテストで確認するのみ)

## 完了条件

- CONNECT ストリームのリセットで `session_ids_` から削除され、`SessionClosed` イベントが発火し、高レベル `Server` の `on_session_closed` が呼ばれる
- モックなしの e2e テストで検証できる (複数セッションを確立し、1 つの CONNECT ストリームをリセットして、`on_session_closed` が正しいセッション ID で 1 回だけ発火して該当セッションのみが終了扱いになり、他セッションのデータ送受信が継続できることを確認する)
- 高レベル `Client` 側でも CONNECT ストリームのリセットで `SessionClosed` が発火し `_connected = False` になることを確認する (高レベル `Server` が `server.reset_stream` でクライアントの CONNECT ストリームをリセットする e2e 構成で検証する)
- 0009 の CONNECT ストリームのフォールバックとテストが本 issue の対応後に整合する (既存テストが引き続き通る)

## 解決方法

`src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` が、CONNECT ストリームのリセット (abrupt) を検知したときにセッション終了の後始末と通知を行うようにした。

- `close_stream` は CONNECT ストリーム (stream_info_ 未登録かつ session_ids_ に含まれる) のリセット時、`nghttp3_conn_close_stream` の同期コールバック (reset_stream_cb / stop_sending_cb / stream_close_cb) の後に `erase_session_streams` でセッションに属するデータストリームの `stream_info_` エントリを清掃し、`session_ids_` から削除して `SessionClosed` イベントを発火するようにした。`SessionClosed` イベントの `error_code` は `close_stream` に渡された QUIC STREAM_RESET のアプリエラーコード、`error_message` は空とした
- 戻り値の復元は削除より先に行う設計を維持したため、1 回目のリセットでは引き続きセッション ID が返り (0009 の `test_stream_reset_connect_stream_session_id` が維持)、2 回目以降の同一 CONNECT ストリームのリセットでは `session_ids_` から削除済みのため -1 が返る
- `src/bindings/webtransport_h3.h` の `close_stream` の docstring を更新した (セッション終了の後始末と通知、error_code の意味論、リセット時の on_stream_reset → on_session_closed の発火順、2 回目以降の戻り値 -1)。`erase_session_streams` の呼び出し元コメントも更新した
- `tests/test_webtransport_h3_stream_buffer_cleanup.py` の stale コメント (「stream_info_ エントリを残す設計の根拠」) を、同期コールバック後に清掃が実行される現行設計に合わせて更新した

テストは `tests/test_e2e_webtransport_h3.py` と `tests/test_webtransport_h3_stream_buffer_cleanup.py` に追加した。モックなしの e2e / Sans-IO 構成で検証する。

- `test_connect_stream_reset_notifies_session_closed`: 同一 QUIC 接続上に 2 セッションを確立し、1 つ目の CONNECT ストリームをリセットすると、サーバー側 `on_session_closed` が正しいセッション ID で 1 回だけ発火し、クライアント側の `SessionClosed` イベントにも `error_code` が伝播し、`session_ids_` から削除され、2 つ目のセッションのデータ送受信が継続できることを確認
- `test_server_resets_client_connect_stream_closes_session`: 高レベル Server が `server.reset_stream` でクライアントの CONNECT ストリームをリセットすると、クライアント側で `SessionClosed` が発火して `is_connected` が False になることを確認
- `test_second_connect_stream_reset_returns_minus_one`: 同一 CONNECT ストリームの 2 回目のリセットで -1 が返ることを確認
- `test_connect_stream_reset_cleans_session_streams`: CONNECT ストリームのリセットでセッションに属するデータストリームの `stream_info_` エントリが清掃され、他セッションのエントリは残ることを確認

なお、新テスト 4 本はいずれも修正前の実装で落ちることを実行確認済み (判別力あり)。
