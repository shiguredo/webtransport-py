# CONNECT ストリームのクリーンクローズ (FIN) でセッション終了の後始末が行われないのを修正する

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-connect-fin-session-cleanup
- Polished: 2026-08-10

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」のうち、クリーンクローズ (FIN) でセッションが終了しても、セッション ID が管理集合 `session_ids_` に残り続け、アプリケーションへの終了通知 (`on_session_closed`) が発火しない問題を修正する。リセット (abrupt) 経路の後始末は 0026 で実装済みであり、本 issue はクリーンクローズ (FIN) 経路の検知を担当する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::initialize` は nghttp3 の `end_stream` コールバックを登録していない (nghttp3 の `nghttp3_callbacks.end_stream` は受信側のストリームが FIN で閉じられたときに発火する。CONNECT ストリームの FIN 受信時にも発火する)
- そのため、CONNECT ストリームの FIN 受信ではセッション終了の検知自体が発生せず、`SessionClosed` イベントが生成されない。高レベル `Server` の `on_session_closed` は呼ばれず、セッション ID は `session_ids_` に残り続ける
- CONNECT ストリームは `stream_info_` に登録されない。`stream_close_cb` は FIN 受信時には発火せず (ストリームの両方向クローズ時のみ発火)、発火したとしても CONNECT ストリームのセッション ID を復元できない
- 0010 の設計方針で「FIN ではセッション終了の検知自体が発生しない。検知経路の追加は本 issue の対象外とする」と線引きされ、0026 でも対象外とされた未対応項目

## 設計方針

- `H3Session::initialize` に `end_stream` コールバックを登録し、CONNECT ストリームの FIN でセッション終了を検知する (CONNECT ストリームの判定は 0026 と同じ `session_ids_` のメンバーシップで行う。データストリームは `session_ids_` に含まれないため誤検知しない)
- `end_stream` コールバックは `receive_stream_data` 経由の `nghttp3_conn_read_stream2` 処理中に同期発火する。コールバック内で nghttp3 を再度呼ぶと再入による状態破壊の恐れがあるため、コールバック内では検知したセッション ID を保留集合へ記録するだけに留め、`session_ids_` の削除・`stream_info_` / `stream_buffers_` の清掃・`SessionClosed` イベント発火・nghttp3 への通知は行わない。`nghttp3_conn_read_stream2` がエラーを返した場合も保留集合をそのまま保持し、`receive_stream_data` の終了時に処理する (エラー由来の `Error` イベントと `SessionClosed` が並ぶ可能性は許容する)
- セッション終了の後始末は、`receive_stream_data` から戻った後に、保留集合のセッション ID に対して 0026 と同じ `close_stream` (`src/bindings/webtransport_h3.cpp` の `H3Session::close_stream`。CONNECT ストリームへの `nghttp3_conn_close_stream` 呼び出しを含む) を実行して行う。これによりリセット経路と同じ経路で、`session_ids_` からの削除・セッションに属するデータストリームの `stream_info_` / `stream_buffers_` 清掃・`SessionClosed` イベント発火に加え、draft-ietf-webtrans-http3-16 Section 6 の MUST (セッションに属するデータストリームの WT_SESSION_GONE による破棄) が満たされる (nghttp3 がデータストリームを WT_SESSION_GONE で破棄し、高レベル層が RESET_STREAM / STOP_SENDING を送出する。純 FIN 経路では nghttp3 が自動で破棄しないため、`close_stream` 呼び出しが MUST 充足の起点になる)
- FIN 経路の `SessionClosed` イベントの `error_code` は 0 とする (リセット経路の `error_code` は QUIC STREAM_RESET のアプリエラーコードだが、FIN 経路は該当しない。draft-ietf-webtrans-http3-16 Section 6 のクリーンクローズ相当の扱い)
- リクエストヘッダーと FIN が同一読み取りで到着した場合 (受理前 FIN) は、`end_headers_cb` の後に `end_stream` が発火し、高レベル層では `on_session_ready` (受理処理) と `on_session_closed` が連続して発火し得る。0026 が受理前リセットを許容したのと対称に、この挙動は許容する。クライアント側で拒否された CONNECT (`reject_session` による 403) のストリーム ID が `session_ids_` に残る既存挙動も同様に許容する (拒否後にピアが FIN を送ると `SessionClosed` が発火し得るが、確立されていないセッションの終了通知として無害)
- 高レベル `Client` 側の SESSION_CLOSED ハンドラ (`src/webtransport/h3/client.py` の `_process_webtransport_events`) は `_connected = False` にするだけで応答 FIN を送出しない。FIN 経路でクライアントがセッション終了を検知した場合、CONNECT ストリームの送信側は開いたまま (half-closed) になる。ピアが完全クローズを待つ場合の相互運用に影響し得るが、本 issue では許容する (ピアへの応答 FIN 送出は別の対応単位として扱う)
- `H3Session::close_stream` の docstring (`src/bindings/webtransport_h3.h`) に、FIN 経路からも呼ばれることと `error_code` が 0 になる意味論を追記する (0026 がリセット経路の docstring を更新したのと対称)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (end_stream コールバックの登録・保留集合・`receive_stream_data` 後の `close_stream` 呼び出し・`close_stream` の docstring 更新)、`src/webtransport/h3/server.py` (必要に応じて。SESSION_CLOSED ハンドラが CONNECT ストリームへ空 FIN を返送する既存挙動 (server.py の `_process_webtransport_events`) が FIN 経路でも正しく機能することをテストで確認する)、テスト (`tests/test_e2e_webtransport_h3.py`。0026 のテスト構成を流用する)

## 完了条件

- CONNECT ストリームの FIN で `session_ids_` から削除され、`SessionClosed` イベントが発火し、高レベル `Server` の `on_session_closed` が呼ばれる (高レベル e2e ではクライアント側の `quic_connection.send_stream_data(session_id, b"", fin=True)` への直接注入で空 FIN を届ける。Sans-IO 構成なら `receive_stream_data(connect_stream_id, b"", fin=True)` で直接渡す)
- 高レベル `Client` 側でも、サーバーが CONNECT ストリームへ空 FIN を送出した場合に `SessionClosed` が発火し、`is_connected` が False になる (0026 の `test_server_resets_client_connect_stream_closes_session` に相当する FIN 版の検証。高レベル API には CONNECT ストリームへ FIN を送出する手段が無いため、テストではサーバー内部の `quic_connection.send_stream_data(session_id, b"", fin=True)` への直接注入で空 FIN を届ける。クライアントはリセットではなく FIN でセッション終了を検知することを確認する)
- FIN 経路の `SessionClosed` イベントの `error_code` は 0 であること (リセット経路の `error_code` は QUIC STREAM_RESET のエラーコードだが、FIN 経路は該当しない。draft-ietf-webtrans-http3-16 Section 6 のクリーンクローズ相当の扱い)
- セッションに属するデータストリームの `stream_info_` エントリが清掃されること (0026 の `test_connect_stream_reset_cleans_session_streams` に相当する検証)
- モックなしのテストで検証できる (0026 のテスト構成を流用する)
