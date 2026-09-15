# 高レベル層の connect() 再入でソケットと Writer が漏れる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-high-level-connect-reentry
- Polished: 2026-09-15

## 目的

`close()` を呼ばずに `connect()` を再度呼ぶと、前回のソケットまたは `StreamWriter` を閉じないまま上書きする。ファイルディスクリプタとストリームが解放されないため、接続の作り直しを繰り返す用途で漏れる。あわせて `Client._connect_one` の失敗経路が `Client._abandon_attempt` を通らず、ソケットが開いたまま残る経路がある。

## 現状

- `src/webtransport/quic/client.py` の `Client.connect` は `self._recv_task` と `self._connecting` を見て `RuntimeError("connect() has already been called")` を送出する。唯一の再入ガードである
- 他の 4 層にはガードが無く、前回の状態を閉じずに上書きする
  - `src/webtransport/h3/client.py` の `Client.connect` は `Client._connect_one` 経由で `self._socket = socket.socket(...)` を再代入する
  - `src/webtransport/http3/client.py` の `Client.connect` も同様に `self._socket` を再代入する
  - `src/webtransport/h2/client.py` の `Client.connect` は `self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(...), ...)` で `StreamWriter` を上書きする
  - `src/webtransport/http2/client.py` の `Client.connect` も `self._reader, self._writer = await asyncio.open_connection(...)` で上書きする
- バックグラウンドの受信タスクを持つのは quic 層のみである。他の 4 層は `Client.run` を明示的に起動する設計であり、タスクの解放漏れは起きない
- `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client.connect` が `Client._abandon_attempt` を呼ぶのは `ConnectTimeoutError` の経路のみで、`HandshakeFailedError` と `ConnectRefusedError` は後始末を通らずに送出され、`self._socket` が開いたまま残る
- `src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` は `self._writer` を `None` に戻さない。`src/webtransport/h2/client.py` と `src/webtransport/h3/client.py` の `Client.close` には「再 connect() の際に前回の状態を引き継がない」旨のコメントがあり、`close()` 後の再接続は契約として存在する (`tests/test_e2e_webtransport_h2.py` の `test_goaway_notified_again_after_reconnect` が同一インスタンスでの再接続を検証している)
- quic 層は `Client.close` でも `self._recv_task` を `None` に戻さないため、`close()` 後の再接続も拒否される。`skills/webtransport-py/SKILL.md` がこの契約 (再試行には新しい `Client` が必要) を記載している
- いずれの層も「接続済みのインスタンスへ再び `connect()` を呼ぶ」ことを禁じる記載が docstring に無い
- `Server.start` も `Server.stop` を挟まずに再呼び出しするとソケットと `asyncio.Server` を上書きする。本 issue の対象外とする

## 設計方針

- `close()` を挟まずに接続を確立したインスタンスへの `connect()` 再入を、4 層で `RuntimeError` として拒否する。誤用を黙って受け入れて前回のリソースを漏らすより、明示的に拒否する方が安全である
- ガードの判定は「確立済み」と「`connect()` 実行中」の 2 条件で行い、quic の `self._recv_task` / `self._connecting` と同じ形に揃える。各層に `connect()` 実行中を表すフラグを追加し、接続確立後に立つ既存のフラグ (`Client._connected`) と組み合わせる。`self._socket` / `self._writer` の非 None を判定に使わない: 前者は `Client._connect_one` の途中で非 None になり、後者は `Client.close` 後も非 None のまま残るため、確立済みを表さない
- `close()` 後の再接続は許容する。`src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` で `self._writer` と `self._reader` を `None` に戻し、ガードが再入を検出しないようにする。quic 層は現行どおり 1 インスタンス 1 回の契約を維持し、本 issue では変更しない
- `Client._connect_one` の失敗経路を揃える。`HandshakeFailedError` と `ConnectRefusedError` でも `Client._abandon_attempt` を通して状態を戻し、失敗後に同じインスタンスで再試行できるようにする。揃えないと、ガードを入れた時点で失敗後の再試行が塞がる
- docstring に再入の契約を明記する。quic 層には既存の `RuntimeError` が未記載のため `Raises` を追記し、4 層には「`close()` の前に再度 `connect()` を呼ぶと `RuntimeError` になる」旨を追記する

## 完了条件

- 接続確立後に `close()` を挟まず `connect()` を呼ぶと、5 層すべてで `RuntimeError` になる
- `connect()` の実行中に再度 `connect()` を呼ぶと、5 層すべてで `RuntimeError` になる
- `connect()` が失敗した後に同じインスタンスで `connect()` を再試行できる (4 層)。失敗経路で `self._socket` / `self._writer` が残らない
- `close()` を挟んだ再接続が 4 層で従来どおり成功する (`tests/test_e2e_webtransport_h2.py` の `test_goaway_notified_again_after_reconnect` が引き続き通過する)
- 各層の docstring に再入の契約が記載される
- `skills/webtransport-py/SKILL.md` の各層 `Client.connect` の説明に再入の契約が記載される
- 上記を検証するテストが 5 層分追加され、全テストが通過する

## 解決方法

- `src/webtransport/h3/client.py` / `src/webtransport/http3/client.py` / `src/webtransport/h2/client.py` / `src/webtransport/http2/client.py` の `Client.connect` の冒頭に、確立済みと実行中を見る再入ガードを追加する (quic の `Client.connect` と同じ形)
- `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client.connect` で、`ConnectTimeoutError` 以外の失敗でも `Client._abandon_attempt` を通す
- `src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` で `self._writer` と `self._reader` を `None` に戻す
- 5 層の `Client.connect` の docstring に再入の契約を追記する
- `skills/webtransport-py/SKILL.md` の各層 `Client.connect` の説明に再入の契約を追記する (quic 層の既存記載と揃える)
- `tests/` に 5 層分の再入テストを追加する。quic 層の再入ガードを検証するテストは現存しないため新規に書く (`tests/test_e2e_quic.py` のサーバー起動と `close` の構成を参考にする)
