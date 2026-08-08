# 高レベル QUIC クライアントに shutdown_stream と wait_for_stream_reset を追加する

- Created: 2026-08-07
- Completed: 2026-08-08
- Branch: feature/add-stream-abort
- Polished: 2026-08-07
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の `shutdown_stream` と `wait_for_stream_reset` を追加し、ストリーム中断系のテストを webtransport-py に置き換えられるようにする。

## 現状

- webtransport-py の高レベル `Client` にはストリーム送受信の中断 API が無い
- 低レベル `Connection` には `close_stream` (RESET_STREAM + STOP_SENDING を送出) / `stop_sending` / `reset_stream` があり、`EventType.STREAM_RESET` イベントも存在する。高レベル `Client` は受信イベントを処理するバックグラウンド受信タスク (0042) を前提とし、`STREAM_RESET` の状態反映もそちらが担う
- sora-quic のテスト (`test_ngtcp2_stop_sending_triggers_reset_stream` / `test_ngtcp2_reset_stream_notifies_controller` / `test_ngtcp2_connection_survives_stream_abort`) が `shutdown_stream` と `wait_for_stream_reset` を使用する (`test_ngtcp2_reset_stream_notifies_controller` は `shutdown_stream` のみ)
- 0025 (RESET_STREAM_AT) は低レベル `reset_stream` を変更対象とし、`close_stream` は NONE フラグのまま変更しないと明記しており、競合しない

## 設計方針

- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client`。コールバック内から `wait_for_stream_reset` を呼び出すと受信処理が進まないため、0037 と同じく `RuntimeError` を raise する
- `shutdown_stream(stream_id, error_code=0) -> None` (async): 低レベル `close_stream` を呼び、RESET_STREAM (RFC 9000 Section 19.4) と STOP_SENDING (Section 19.5) をスケジュールする。低レベル `close_stream` はフレームを書くだけで、実際の送出は `_send_pending()` 経由 (既存の `send_stream_data` と同じパターン)。双方向ストリームでは両方を送出する。単方向ストリームでは `ngtcp2_conn_shutdown_stream` がローカル単方向なら write 側 (RESET_STREAM) のみ、リモート単方向なら read 側 (STOP_SENDING) のみを shutdown する
- RESET_STREAM の送出は状態依存である。`ngtcp2_conn_shutdown_stream` は書き込み側が全データ送信済み + FIN 確認済みの場合は RESET_STREAM を送出しない (書き込み側が既に完了しているため)
- `wait_for_stream_reset(stream_id, timeout=10.0) -> int`: ピアの RESET_STREAM 受信を待ち、そのアプリケーションエラーコードを返す。`STREAM_RESET` イベントの処理とストリームごとのエラーコード保持はバックグラウンド受信タスク (0042) が担い、`wait_for_stream_reset` はその状態を待つ。呼び出し時点で既に RESET_STREAM を受信済みのストリームは即時 return する。期限までに受信しない場合、または接続終了 (CONNECTION_CLOSED) を受信した場合は `TimeoutError` を raise する
- `recv_stream_data` (0037) 待機中の STREAM_RESET 受信時の挙動は 0037 で定義済み (進捗として idle deadline 延長 → `overall_timeout` で `TimeoutError`) であり、本 issue はこれを変更しない。追加の受信状態管理は不要
- ngtcp2 は STOP_SENDING を受信すると、ピアの送信側の全データと FIN が ACK 済みでない限り、自動で RESET_STREAM を送出する (RFC 9000 Section 3.5 の MUST。エラーコードは STOP_SENDING から複製する SHOULD)。そのため `wait_for_stream_reset` は通常すぐにエラーコードを受け取る。`TimeoutError` の検証は、ピアが自動応答しない「双方向ストリームが完結済み (write 側全 ACK / read 側 FIN 受信済み) で、`shutdown_stream` を呼んでも何も送出されず、`wait_for_stream_reset` がタイムアウトする」経路で行う。テストはエコー往復 (`send_stream_data(..., fin=True)` → `recv_stream_data`) でストリームを完結済みにしてから `shutdown_stream` を呼ぶ手順で行う
- クライアントが送出した RESET_STREAM の検証は、Sans-IO 実通信テストでピア側の STREAM_RESET イベント受信を確認して行う (webtransport-py の高レベル `Server` は STREAM_RESET を処理しないため、サーバー側観測による検証は行わない)
- エラーコードの複製テストでは、ピア (webtransport-py `Server`) の送信側を Ready/Send に留める必要がある (エコーを `fin=False` で返す等)。`fin=True` でエコーすると全 ACK 到達後に STOP_SENDING への RESET_STREAM 自動応答が出なくなる (RFC 9000 Section 3.5 の Data Sent 状態の MAY / ngtcp2 の実装挙動)
- 上記の自動応答・早期 return は ngtcp2 の実装挙動に依存するため、ngtcp2 のバージョン更新時に再確認する
- 接続終了 (CONNECTION_CLOSED) と RESET_STREAM 受信は区別する。0042 は接続終了を待機者へ別経路で通知し、`wait_for_stream_reset` は受信済みエラーコードの誤参照 (KeyError 等) を起こさないようにする
- webtransport-py のバインディングは STOP_SENDING 受信をイベントとして公開していないため、`shutdown_stream` による STOP_SENDING 送出は、ピアが応答した RESET_STREAM のエラーコードの往復で間接的に検証する

## 完了条件

- `shutdown_stream` で双方向ストリームの RESET_STREAM と STOP_SENDING を送出できる
- `wait_for_stream_reset` がピアの RESET_STREAM が運んだエラーコードを返す
- 期限までに RESET_STREAM を受信しない場合、および接続終了した場合は `TimeoutError` を raise する
- 中断したストリームとは別のストリームでデータ転送が継続できる
- テストを追加する (エラーコードの複製 / クライアント側の RESET_STREAM 送出 (Sans-IO ピア観測) / タイムアウト / 接続終了からの `TimeoutError` / ストリーム中断後の接続生存 / STREAM_RESET 受信時の recv_stream_data の挙動維持 / 既に RESET 受信済みストリームの即時 return / ローカル単方向ストリームの shutdown 分岐)。エラーコードの複製は RFC 9000 Section 3.5 の SHOULD であり、ピア (ngtcp2) が複製する前提で検証する。テストは 0042 (バックグラウンド受信タスク) の実装後に実施する
- 既存の全テストが通る

## 解決方法

- `src/webtransport/quic/client.py` に `shutdown_stream(stream_id, error_code=0) -> None` を追加した。低レベル `Connection.close_stream` を呼び、RESET_STREAM (RFC 9000 Section 19.4) と STOP_SENDING (Section 19.5) をスケジュールして `_send_pending()` で送出する。双方向ストリームでは両方を送出し、ローカル単方向ストリームでは write 側 (RESET_STREAM) のみを shutdown する
- `wait_for_stream_reset(stream_id, timeout=10.0) -> int` を追加した。STREAM_RESET のエラーコードを `_StreamRecvState.reset_error_code` に記録し、その状態を待って返す。呼び出し時点で受信済みなら即時 return し、期限までに受信しない場合・接続終了時は TimeoutError を raise する
- `_notify_stream_progress` を `_handle_stream_reset` に改名し、STREAM_RESET のエラーコードを受信状態に記録するようにした (recv_stream_data は進捗として idle deadline を 1 回延長する従来挙動を維持)
- recv_stream_data の「タイムアウトと同時刻の進捗検出」を event 状態ではなく永続状態 (受信バイト数 / エラーコード) の比較に変更した。wait_for_stream_reset と同一ストリームで並行待機しても event の clear 競合で進捗を見失わない
- コールバック内からの呼び出しは contextvars で検出し、wait_for_stream_reset は RuntimeError を raise する (shutdown_stream はフレームのスケジュールと送出のみで待機しないためコールバック内から呼べる)
- テストは `tests/test_e2e_quic_stream_abort.py` に 11 件を追加した (エラーコードの複製 / 完結済みストリームのタイムアウト / 接続終了からの TimeoutError / ストリーム中断後の接続生存 / 既に RESET 受信済みの即時 return / ローカル単方向の shutdown 分岐 / STREAM_RESET 受信時の recv_stream_data の挙動維持 / 0 以下の timeout の ValueError / コールバック内からの RuntimeError / コールバック内からの shutdown_stream / Sans-IO ピア観測による close_stream の RESET_STREAM 送出)
- `skills/webtransport-py/SKILL.md` の `quic.Client` 節に `shutdown_stream` / `wait_for_stream_reset` の説明を追加した
- `CHANGES.md` の `## develop` セクションに `[ADD]` エントリを追加した
