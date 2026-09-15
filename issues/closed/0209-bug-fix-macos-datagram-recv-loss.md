# macOS で高レベル API の受信パケットが取りこぼされるのを修正する

- Created: 2026-09-15
- Completed: 2026-09-15
- Branch: feature/fix-macos-udp-recv-loss
- Polished: {YYYY-MM-DD}

## 目的

macOS の kqueue セレクタで、`quic` / `h3` / `http3` の高レベル API が受信パケットを取りこぼす問題を修正する。取りこぼしたパケットはピアの再送 (PTO) まで届かず、ハンドシェイク中のパケットが失われると PTO のバックオフで数秒単位の遅延になる。この遅延が CI の e2e テストのタイムアウトとして現れ、macOS ジョブが繰り返し失敗している。

## 現状

高レベル API の受信ループは、`socket.socket` を非ブロッキングにして `loop.sock_recvfrom` を `asyncio.wait_for` で包み、タイムアウト時は何もせず呼び出し側に戻る。対象は次の 6 箇所。

- `src/webtransport/quic/client.py` の `Client._receive`
- `src/webtransport/quic/server.py` の `Server.run`
- `src/webtransport/h3/client.py` の `Client._receive`
- `src/webtransport/h3/server.py` の `Server.run`
- `src/webtransport/http3/client.py` の `Client._receive`
- `src/webtransport/http3/server.py` の `Server.run`

`loop.sock_recvfrom` はソケットをノンブロッキングで 1 回読み、読めなければ `add_reader` でセレクタに登録して待つ。`asyncio.wait_for` のタイムアウトはこの待機 future をキャンセルし、その `done_callback` がセレクタ登録を解除する。解除の直前に届いたパケットは読み取り可能通知が失われ、次のループで `sock_recvfrom` を呼び直しても、再登録とデータ到着の順序によっては通知が届かない。

macOS の kqueue セレクタで再現する。UDP ソケットをペアにして、`loop.sock_recvfrom` を `asyncio.wait_for` で包んだ受信を繰り返しながら 1 ms 間隔でパケットを送ると、1500 パケットのうち数個から十数個が受信できず、送信側が送り終えた後も受信ループがタイムアウトを繰り返す (待機上限 1 ms、1500 パケットで 3 回中 3 回再現。`asyncio.timeout` でも同じ)。`loop.add_reader` で待機専用の future を使う方式では同じ条件で取りこぼしが起きない。

CI では `wheel` ワークフローの macOS ジョブが断続的に失敗している。2026-09-14 の run 34851478562 (macOS 3.14t) では `tests/test_e2e_webtransport_h3_low_level.py::test_connect_stream_fin_notifies_session_closed` が `client.connect()` のタイムアウトで失敗し、1130 passed / 1 failed だった。同種の失敗は `test_e2e_quic_advanced.py::test_early_data_send_receive` や `test_e2e_http3.py::test_server_on_connection_error_fires_on_protocol_error` でも観測されている。いずれも Ubuntu ジョブでは再現しない。

## 設計方針

待機と受信を `src/webtransport/_common.py` の共通ヘルパーに集約し、6 箇所の受信ループを差し替える。

- `wait_socket_readable(sock, timeout) -> bool` を追加する。待機専用の future を `loop.add_reader` に登録し、タイムアウトは `loop.call_later` で扱う。`asyncio.wait_for` によるタスクのキャンセルに依存しないため、セレクタ登録の解除は `finally` で 1 回だけ起きる。`asyncio.wait_for` のキャンセルと `sock_recvfrom` の解除が競合しないので通知を失わない
- `recv_datagram(sock, timeout) -> tuple[bytes, tuple[Any, ...]] | None` を追加する。待機の前にソケットを直接読み、受信済みなら待たずに返す。待機中に届いたパケットを取りこぼさないことと、受信キューに溜まっているときに無駄なウェイクアップを挟まないことを両立する
- 6 箇所の受信ループは `recv_datagram` に置き換える。既存の「受信できたら non-blocking で読み切る」構造はそのまま残す
- 呼び出し側が `.venv` の socket を直接扱う現状の構造 (Sans-IO な低レベル API と asyncio を組み合わせる利用者) は変えない
- 再発防止として `tests/test_common.py` に取りこぼしの回帰テストを追加する。モックは使わず、ループバックの UDP ソケットで実際に送受信する

## 完了条件

- 取りこぼしの再現テストが追加され、修正前の実装では失敗し、修正後は連続して通過すること
- 全テストが通過すること
- CI (wheel ワークフロー) の macOS ジョブが通過すること
- `CHANGES.md` に FIX エントリが追加されていること

## 解決方法

- `src/webtransport/_common.py` に `wait_socket_readable` と `recv_datagram` を追加した
- `src/webtransport/quic/client.py` / `src/webtransport/quic/server.py` / `src/webtransport/h3/client.py` / `src/webtransport/h3/server.py` / `src/webtransport/http3/client.py` / `src/webtransport/http3/server.py` の受信待ちを `recv_datagram` に置き換えた
- `tests/test_common.py` を新設し、`wait_socket_readable` と `recv_datagram` の基本動作と、タイムアウトをまたいでもパケットを取りこぼさないことを検証する
- `tests/test_e2e_webtransport_h3_low_level.py` の `_LowLevelClient._receive` も同じヘルパーを使うようにした

### 検証結果

- 追加した回帰テストは、`asyncio.wait_for` で `loop.sock_recvfrom` を包む旧実装では 3 回中 3 回失敗し、修正後は 5 回連続して通過した
- 全テスト (1137 件) が通過した
- `prek run --all-files` の全フックが通過した
- スループット系テスト (`tests/test_e2e_http3_throughput.py` / `tests/test_e2e_quic_throughput.py` / `tests/test_e2e_webtransport_h3_throughput.py` / `tests/test_e2e_webtransport_h2_throughput.py`) が通過し、性能劣化が無いことを確認した
