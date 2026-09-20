# http3 の高レベル層がピアの RESET_STREAM を nghttp3 へ伝えない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-peer-reset-not-forwarded
- Polished: 2026-09-20

## 目的

`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は、QUIC の `STREAM_RESET` を受信するとアプリのコールバック (`on_stream_reset`) を呼ぶだけで、低レベルの `Http3Connection` に読み取り中断を伝えない。そのため nghttp3 はリセットされたストリームの受信側を生存として扱い続け、`Http3Connection::pending_headers_` (受信途中のヘッダーブロック) が接続終了まで残る。ピアが「部分的な HEADERS + RESET_STREAM」を繰り返すと、ストリームを張り替えるたびにエントリが積み上がる。

## 現状

- `src/webtransport/http3/client.py` と `server.py` の `STREAM_RESET` 分岐は `self._on_stream_reset(...)` を呼ぶだけである
- `Http3Connection.reset_stream` の呼び出し元は `Client.reset_stream` と `Server.reset_stream` のみで、どちらもアプリが明示的に呼ぶ公開 API である。アプリがピアのリセットを受けて応答として `reset_stream` を呼んだ場合にのみ到達し、呼ばない限り到達しない
- `Http3Connection.close_stream` は `src/webtransport/http3/` からは呼ばれていない (残りの DATA イベントを落とすため、QUIC FIN による終端検知では `nghttp3_conn_close_stream` を意図的に避けている)
- `Http3Connection` に読み取り中断だけを行う API は無い。`shutdown_stream_write` (読み取り側ではなく書き込み側) と `reset_stream` / `close_stream` だけである。`Http3Connection.reset_stream` は `nghttp3_conn_shutdown_stream_read` (受信側の中断。RFC 9114 Section 4.1.1 の「aborts reading on the receiving parts of streams」) と `ResetStream` イベントの push を同時に行う
- `Http3Connection::pending_headers_` の解放は 3 経路 (`reset_stream` / `stream_close_cb` / `reset_stream_cb`) に追加済みである。ただし高レベル層がリセットを転送しないため、ピア起点のリセットではこの解放に到達しない
- `ResetStream` イベントは高レベル層で無条件に QUIC の `RESET_STREAM` 送出に変換される (client.py / server.py のイベントループ)。そのため転送に `Http3Connection.reset_stream` をそのまま使うと、ピアが既にリセットしたストリームへ `RESET_STREAM` を返送することになる
- WebTransport over HTTP/3 の本番経路 (`src/webtransport/h3/`) は `H3Session` を使い `Http3Connection` を生成しないため、本 issue の影響は素の HTTP/3 の利用者に限られる
- `Http3Connection::has_pending_headers` (`_has_pending_headers`) は受信途中のヘッダーブロックの解放を観測するテスト専用 API として追加済みである (0215 で develop にマージ済み)

## 設計方針

- **採用案**: `Http3Connection::shutdown_stream_read(int64_t stream_id)` を追加し、`STREAM_RESET` の受信時に高レベル層から呼ぶ
  - 実装は `nghttp3_conn_shutdown_stream_read` を呼んだうえで、`stream_buffers_` と `pending_headers_` を削除する (`nghttp3_conn_shutdown_stream_read` は `stream_close` コールバックを呼ばないため、明示的に解放しないとエントリが残る)。`stream_buffers_` はピアが放棄したストリームへの未送信応答データであり、保持し続けても送出の機会が無いため `reset_stream` と同じ後始末として削除する。イベントは push しない
  - 入力契約は `reset_stream` と揃える (接続が無い・閉じている場合は no-op、`stream_id` が 0 未満または 2^62-1 超は黙って無視)
  - `shutdown_stream_ids_` (書き込み側の shutdown 記録) には触れない。読み取りの中断は書き込み側の状態を変えない
  - `Http3Connection.reset_stream` は `ResetStream` イベントを push するため、高レベル層から呼ぶとピアが既にリセットしたストリームへ QUIC の `RESET_STREAM` を返送してしまう。この理由で `reset_stream` の再利用はしない
- **不採用**: `Http3Connection.close_stream` を呼ぶ案。`nghttp3_conn_close_stream` で nghttp3 のストリームが削除され `StreamEnd` イベントが push されるため返送は起きないが、`conn_delete_stream` がストリームと受信キューを破棄するため、ピアのリセット後に届き得るデータを落とす。また現行コードは QUIC FIN の終端検知で `nghttp3_conn_close_stream` を意図的に避けており (残りの DATA イベントを落とさないため)、同じ判断をピア起点のリセットにも適用する。読み取り中断だけなら nghttp3 は `SHUT_RD` を立てて以後のデータを消費・破棄するため、ストリームの寿命を変えずに目的 (エントリの解放) を満たせる
- アプリの `on_stream_reset` コールバックの呼び出しと引数は変えない (転送はコールバックの前後どちらでもよい)
- 変更対象: `src/bindings/http3.h` / `src/bindings/http3.cpp` (新メソッドとバインディング)、`src/webtransport/webtransport_ext/http3.pyi` (再生成)、`src/webtransport/http3/client.py` / `src/webtransport/http3/server.py` (STREAM_RESET 分岐)、`skills/webtransport-py/SKILL.md` (`http3.Connection` のメソッド一覧に追記)、`tests/` (検証テスト。既存の `tests/test_http3.py` の docstring が「高レベル層は転送しない」と述べているため更新する)

## 完了条件

- ピアから `STREAM_RESET` を受信したときに、低レベルの `Http3Connection` へ読み取り中断が伝わる (`shutdown_stream_read` が呼ばれる)
- 「部分的な HEADERS を送ったあとピアが `RESET_STREAM` を送る」状況を実 QUIC のピアで再現し、`_has_pending_headers` でエントリの解放を観測する
  - ピアは `webtransport.quic.Client` を実 UDP で高レベル `http3.Server` に接続し、`open_stream(bidirectional=True)` で開いたストリームへ部分的な HEADERS フレーム (Length を実際のペイロードより大きく宣言した 1 バイトの Type + Length + 不完全なフィールドセクション) を送る。この時点で DUT 側の `_has_pending_headers(stream_id)` が `True` になることを表明する
  - リセットは `quic.Client.shutdown_stream` ではなく低レベル `quic.Client._connection.reset_stream(stream_id, error_code)` で送る。`quic.Client.shutdown_stream` は STOP_SENDING も送出するため、RFC 9000 Section 3.5 の MUST により DUT の QUIC 層が修正の有無にかかわらず `RESET_STREAM` を返し、「返送しない」ことを検証できない
  - 低レベル `reset_stream` はフレームをキューに積むだけで送出しないため、`await quic.Client._send_pending()` で明示的にフラッシュする (背景ループも送出するが、受信待ちの間は次回の起床まで送られない)
  - 観測は `server._clients[addr].http3_connection._has_pending_headers(stream_id)` が `None` になること (既存 e2e テストと同じ内部アクセスの慣行)
- ピアが既にリセットしたストリームへ `RESET_STREAM` を返送しないことを検証する
  - ピア側の QUIC 接続が当該ストリームのリセット通知を受け取らないこと (`quic.Client.wait_for_stream_reset` を短い期限で呼び、`TimeoutError` になることで確認する。高レベル層の run ループは `ResetStream` イベントを毎周回 drain して即座に QUIC の `RESET_STREAM` 送出へ変換するため、DUT 側の `next_event()` を外から観測しても誤実装を検出できない)
- 「読み取り中断はイベントを push しない」契約を、高レベル層の run ループを介さない低レベル単体テスト (`http3.Connection` のクライアント・サーバーペア) で固定する。`shutdown_stream_read` の後に `_has_pending_headers` が `None` になり、`next_event()` に `ResetStream` が積まれないことを確認する
- アプリの `on_stream_reset` コールバックが従来どおり呼ばれることを検証する (ストリーム ID とエラーコードが一致すること)
- 全テストが通過する

## 対象外

- 高レベル層の QUIC FIN 終端 (`STREAM_END` / 受信完了) の扱い (本 issue はピア起点の `RESET_STREAM` のみを対象とする)
- アプリが明示的に呼ぶ `Client.reset_stream` / `Server.reset_stream` の挙動 (`ResetStream` イベントを push して QUIC `RESET_STREAM` を送出する現行仕様を維持する)
- WebTransport over HTTP/3 の本番経路 (`src/webtransport/h3/`) のリセット転送 (既に `H3Session::close_stream` で実装済み)
- nghttp3 のストリームオブジェクト自体の解放 (読み取り中断では残るが、FIN 終端の既存経路と同じ性質であり接続終了で解放される)
