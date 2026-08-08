# 高レベル QUIC クライアントにバックグラウンド受信タスクを導入する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-background-recv-task
- Polished: 2026-08-08
- Reporter: @voluntas

## 目的

高レベル `Client` が `run()` を明示起動しなくても受信イベントを処理できるようにし、sora-quic のテスト置き換え (ngtcp2-py から webtransport-py へ) で使われる `recv_stream_data` (0037) と `wait_for_stream_reset` (0038) の前提を用意する。

## 現状

- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client`。webtransport-py の高レベル `Client` はバックグラウンドタスクを持たず、`connect()` はハンドシェイク完了で return する。受信イベント (`STREAM_DATA` / `DATAGRAM` 等) の処理、ACK 送出 (`_send_pending()`)、タイマー処理 (`get_timeout()` / `handle_timeout()`) を担うのは `run()` のみで、`run()` は接続終了までブロックする (明示的に `asyncio.create_task(client.run())` で起動する必要がある)
- sora-quic のテストは `run()` を呼ばず `connect()` → `send_stream_data()` → `recv_stream_data()` の形で使用するため、このまま置き換えると受信イベントが処理されず `recv_stream_data` は永遠にデータを受け取れない
- ngtcp2-py は `connect()` が service task + socket reader task を起動し、`recv_stream_data` / `wait_for_stream_reset` はそのタスクが供給するイベントを待つ構造である

## 設計方針

- `connect()` がバックグラウンド受信タスクを起動する (ngtcp2-py の service task 構造を参考に、受信ループ全体を担う)。`connect()` はコネクション生成 → `_flush_early_data()` (0-RTT の送出) → 初期 `_send_pending()` (Initial パケット送出) の既存の順序を維持した後、バックグラウンドタスクを起動する。受信ループは現行 `run()` の責務を引き継ぐ: socket の読み取り (`_receive()`)、イベントの取り込み・処理、ACK / フロー制御 / ハンドシェイク継続パケットの送出 (`_send_pending()`)、タイマー処理 (`get_timeout()` / `handle_timeout()` による PTO 再送・アイドルタイムアウト検知)。`handle_timeout()` を欠くとアイドルタイムアウトによる接続終了 (CONNECTION_CLOSED) が生成されず、0037 / 0038 の「接続終了からの `TimeoutError`」の前提が破れる
- `connect()` のハンドシェイク完了待ちもバックグラウンドタスク内で検知するよう再構成し、`connect()` は完了を待つだけで return する。起床経路は次の 4 経路を定義する: ハンドシェイク完了 (現行どおり `True` を返す)、接続終了 CONNECTION_CLOSED (現行どおり `False` を返す。既存テスト `test_verify_peer_rejects_self_signed` が依存)、タスク異常終了 (待機中の `connect()` へ元の例外を伝播する。コールバック内の例外もこの経路で伝播する)、`close()` によるタスク停止 (正常終了。`False` を返す。タスクが終了フラグの設定に従って停止し、`connect()` が永久ブロックしない)。0039 のタイムアウト (未完了で `False`) はタスクを継続したまま `connect()` だけを return する経路であり、0042 の構造の上に 0039 が実装する
- バックグラウンドタスクは受信イベントを処理して、ストリームごとの受信状態を更新する。受信状態の構造と待機 API は 0037 (`recv_stream_data` の累積連結・FIN 検出・完了判定) と 0038 (`STREAM_RESET` のエラーコード保持) が定義し、0042 のタスクが定義された状態構造への更新を担う。実装順序は 0042 を先に実装する (0039 も「0042 を先に実装し、その上でタイムアウトを実装する」)。0042 を先に実装するため、受信状態の構造は 0042 の実装時点では未定義であり、0042 単体では状態更新の動作を検証できない。0042 側では `connect()` 後の受信イベント処理とコールバック発火、および接続終了・タスク異常終了の通知を自前で検証できるテストを用意し、状態更新の動作検証は 0037 / 0038 の各 issue で行う
- 接続終了 (CONNECTION_CLOSED) を検知した場合、待機中の `recv_stream_data` (0037) / `wait_for_stream_reset` (0038) を起床して通知する。通知は「接続終了を待機者へ伝える共有経路」に対して行い、待機側の `TimeoutError` raise は 0037 / 0038 の定義に委ねる (0042 は起床・通知の機構のみを実装する)。タスク異常終了の場合は、ngtcp2-py の `_raise_service_task_error` と同じく元の例外を待機側に伝播する機構を実装する (例外を保持して各待機者で raise する。複数待機者への同時伝播と、タスク終了後に新規に待機を始めた場合の挙動を含める)
- `run()` は「バックグラウンドタスクの完了 (接続終了) まで待つ」だけに役割を変更する (受信処理はバックグラウンドタスクが担い、二重受信は構造上発生しない)。既存テストの `create_task(client.run())` パターンは接続終了待ちとして引き続き動作する。`run()` のキャンセルがバックグラウンドタスクへ伝播しないよう (既存テストの `client_task.cancel()` は run() のみを止め、受信タスクは `close()` まで継続する)、`asyncio.shield()` 等で受信タスクを保護する。`run()` が待つタスクが異常終了した場合は、`run()` の待機者へ元の例外を伝播する (`connect()` への伝播と同じ扱い)。`connect()` 未呼び出し・タスク未起動時に `run()` を呼んだ場合は現行どおり `RuntimeError` を raise する
- `close()` はバックグラウンドタスクを終了フラグで停止し、完了を待ってから socket を閉じる。停止手順は「終了フラグ設定 → タスク完了待ち → `connection.close()` → `_send_pending()` (CONNECTION_CLOSE 送出。0030 の挙動を維持) → socket クローズ」の順とする (CONNECTION_CLOSE 送出を落とすと、ピアは接続終了を検知できずアイドルタイムアウトまで保持する)。タスク完了待ちの `await` がタスクの例外を伝播しないよう、`asyncio.gather(..., return_exceptions=True)` 等で例外を握り、CONNECTION_CLOSE 送出と socket クローズを必ず実行する (close() 中にタスクが異常終了しても、`_send_pending()` が `OSError` を raise しても、socket クローズは `finally` で保証する)。`migrate()` はタスクを継続したまま旧 socket を閉じる。`_receive()` が 0.1 秒の `wait_for` タイムアウトで定期起床し `self._socket` を再参照するため、旧 socket のクローズ後も次回から新 socket で受信を継続できる。タスクは受信時の `OSError` を捕捉し、終了フラグが立っていれば正常終了、立っていなければ `self._socket` を再参照して受信を継続する (migrate / 0040 のソケット差し替え由来の旧 socket クローズ・FD 再利用は再参照で解消する一時的な `OSError`)。再参照しても継続する恒久的な受信 `OSError` は、送信側と同じくタスク異常終了として扱う (busy-loop を避けるため)。`_send_pending()` 等の送信側の `OSError` は捕捉して継続しない (永続的な失敗で busy-loop するため、タスク異常終了として扱う)
- 既存のコールバック (`on_handshake_completed` / `on_stream_data` / `on_datagram` / `on_connection_closed` / `on_session_ticket` / `on_early_data_rejected`) の挙動は維持する。ハンドシェイク完了検知はタスクが行い、`on_handshake_completed` の完了後に `connect()` を起床して return させる (コールバック完了と `connect()` の return の順序を保証する)。現行の `connect()` は STREAM_DATA / DATAGRAM を処理しないため、これらのコールバックの発火タイミング (現行は `run()` 起動後) がタスク起動後に変わり得るが、発火条件と引数は維持する。同様に、現行の `connect()` は CONNECTION_CLOSED 時に `on_connection_closed` を発火しない (発火するのは `run()` のみ) が、新設計ではタスクが統合ループを持つため `connect()` 待機中にも発火し得る (発火条件そのものは維持し、`connect()` は起床経路どおり `False` を返す)。ハンドシェイク完了と `close()` が競合した場合はハンドシェイク完了を優先し、`connect()` は `True` を返す。コールバックはタスク内で await されるため、コールバック内から `close()` を呼ぶとタスクが自分自身を await して `RuntimeError` になる。再入を防ぐため、タスク内からの呼び出しでは完了待ちをスキップする等の再入ガードを設ける (コールバック内から `close()` を呼ぶケースを許容する)。コールバック内で `close()` を呼んだ場合、タスクはコールバック復帰後に終了フラグを確認してループを抜ける (クローズ済み socket での `_send_pending()` 呼び出しによる送信側 `OSError` を避ける)。`connect()` の再呼び出しでバックグラウンドタスクが二重に起動しないよう、`connect()` は 2 回目以降 `RuntimeError` を raise する
- タスクの停止に使う終了フラグは、既存の `_running` を流用する (現行 `connect()` / `run()` のループ条件と `close()` の停止に使うフラグ。新設計では `_running` をタスクの終了フラグとして使い、`close()` が `False` にする)。`_connected` フラグの更新はバックグラウンドタスクと `close()` が担う (ハンドシェイク完了で `True`、接続終了・`close()` で `False`。現行 `run()` / `connect()` / `close()` の責務を引き継ぐ)。`is_connected` プロパティと `register_early_data` のガードはこのフラグに依存するため、更新タイミング (接続終了を検知した時点ですぐ `False` にすること) と、既に終了した接続への `register_early_data` が受理されないこと (`RuntimeError` を raise し続けること) に注意する

## 完了条件

- `run()` を明示起動しなくても受信イベントが処理され、ストリームごとの受信状態 (0037 / 0038 が定義するもの) が更新される (状態更新の動作検証は 0037 / 0038 の各 issue で行う)。0042 単体では、`connect()` 後に `run()` なしで受信イベントが処理されコールバックが発火することを検証する
- 既存の `run()` ベースのテスト (`create_task(client.run())` パターン) が引き続き通る。受信処理の前提が変わるテストは、意図を維持する形で更新する (例: `test_cwnd_exhaustion_does_not_hang` は「run() を回さないと ACK 処理が進まない」前提で成り立っており、バックグラウンドタスク導入後はこの前提が再現不能になるため、cwnd 枯渇の検証方法を別手段で再設計する)
- バックグラウンドタスクと `run()` の二重受信・競合が構造上発生しない
- 接続終了・タスク異常終了を待機者へ通知する機構が実装される。0042 単体では待機者 (`recv_stream_data` / `wait_for_stream_reset`) が存在しないため、待機者への通知の検証は 0037 / 0038 の各 issue の完了条件に追記して行う (0042 側では、タスク異常終了が `connect()` の待機者へ伝播することを自前で検証する)
- `close()` でバックグラウンドタスクが停止し、タスクの異常終了として誤検知されない (コールバック内から `close()` を呼んだ場合も、タスクは正常終了し異常終了扱いにならない)。CONNECTION_CLOSE 送出 (0030) が維持される
- `migrate()` 後も受信が継続する
- テストを追加する (connect() 後に `run()` なしで受信イベントが処理されコールバックが発火する / `close()` でタスクが停止する / `migrate()` 後に受信が継続する / タスク異常終了時に `connect()` の待機者へ例外が伝播する)
- `skills/webtransport-py/SKILL.md` の `run()` の記述 (受信ループ) を新役割 (バックグラウンドタスクの完了待ち) に合わせて更新する
- 既存の全テストが通る

## 解決方法

(実装時に追記する)
