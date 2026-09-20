# 高レベル層の connect() 再入でソケットと Writer が漏れる

- Created: 2026-09-15
- Completed: 2026-09-20
- Branch: feature/fix-high-level-connect-reentry
- Polished: 2026-09-15

## 目的

`close()` を呼ばずに `connect()` を再度呼ぶと、前回のソケットまたは `StreamWriter` を閉じないまま上書きする。ファイルディスクリプタとストリームが解放されないため、接続の作り直しを繰り返す用途で漏れる。調査の結果、`src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client._connect_one` の失敗経路は既に `Client._abandon_attempt` を通っており、本 issue では再入ガードの追加と `close()` 後の再接続契約の明文化を行う。

## 現状

- `src/webtransport/quic/client.py` の `Client.connect` は `self._recv_task` と `self._connecting` を見て `RuntimeError("connect() has already been called")` を送出する。唯一の再入ガードである
- 他の 4 層にはガードが無く、前回の状態を閉じずに上書きする
  - `src/webtransport/h3/client.py` の `Client.connect` は `Client._connect_one` 経由で `self._socket = socket.socket(...)` を再代入する
  - `src/webtransport/http3/client.py` の `Client.connect` も同様に `self._socket` を再代入する
  - `src/webtransport/h2/client.py` の `Client.connect` は `self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(...), ...)` で `StreamWriter` を上書きする
  - `src/webtransport/http2/client.py` の `Client.connect` も `self._reader, self._writer = await asyncio.open_connection(...)` で上書きする
- バックグラウンドの受信タスクを持つのは quic 層のみである。他の 4 層は `Client.run` を明示的に起動する設計であり、タスクの解放漏れは起きない
- `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client._connect_one` は `except (OSError, WebTransportConnectError)` で `Client._abandon_attempt` を通るため、`HandshakeFailedError` と `ConnectRefusedError` でも後始末される (例外は `WebTransportConnectError` の派生)。`Client.connect` の候補ループ側も `ConnectTimeoutError` で `_abandon_attempt` を通る。したがって失敗経路の後始末は既に揃っている
- `src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` は `self._writer` を `None` に戻さない。`src/webtransport/h2/client.py` と `src/webtransport/h3/client.py` の `Client.close` には「再 connect() の際に前回の状態を引き継がない」旨のコメントがあり、`close()` 後の再接続は契約として存在する (`tests/test_e2e_webtransport_h2.py` の `test_goaway_notified_again_after_reconnect` が同一インスタンスでの再接続を検証している)
- quic 層は `Client.close` でも `self._recv_task` を `None` に戻さないため、`close()` 後の再接続も拒否される。`skills/webtransport-py/SKILL.md` がこの契約 (再試行には新しい `Client` が必要) を記載している
- いずれの層も「接続済みのインスタンスへ再び `connect()` を呼ぶ」ことを禁じる記載が docstring に無い
- `Server.start` も `Server.stop` を挟まずに再呼び出しするとソケットと `asyncio.Server` を上書きする。本 issue の対象外とする

## 設計方針

- `close()` を挟まずに接続を確立したインスタンスへの `connect()` 再入を、4 層で `RuntimeError` として拒否する。誤用を黙って受け入れて前回のリソースを漏らすより、明示的に拒否する方が安全である
- ガードの判定は「確立済み」「`connect()` 実行中」「前回の接続に使った transport (ソケット / `StreamWriter`) が生存」の 3 条件で行う。各層に `connect()` 実行中を表すフラグを追加し、接続確立後に立つ既存のフラグ (`Client._connected`) と組み合わせる。transport の生存も条件に含めるのは、ピアがセッション / 接続を閉じると `Client._connected` は False になるが transport は残るためである (その状態で再入すると前回の transport を閉じないまま上書きして漏れる)。`Client.close` は transport を破棄してから戻るため、`close()` の後は再入が通る
- `close()` 後の再接続は許容する。`src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` で `self._writer` と `self._reader` を `None` に戻し、ガードが再入を検出しないようにする。quic 層は再接続契約 (1 インスタンス 1 回) を変えないが、実行中フラグを名前解決の前へ移して名前解決中の再入も拒否する
- `Client._connect_one` の失敗経路の後始末は既に `Client._abandon_attempt` を通るため変更しない。再入ガードを入れた後に失敗後の再試行が塞がれないことをテストで確認する (`_abandon_attempt` が `_connected` を戻すため再試行できる)
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

- `src/webtransport/h3/client.py` / `src/webtransport/http3/client.py` / `src/webtransport/h2/client.py` / `src/webtransport/http2/client.py` の `Client.connect` の冒頭に、確立済み・実行中・transport の生存を見る再入ガードを追加する。あわせて `src/webtransport/quic/client.py` は実行中フラグを名前解決の前へ移し、名前解決中の再入も拒否する
- `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client._connect_one` の失敗経路は既存の `Client._abandon_attempt` 呼び出しで足りるため変更しない (確認のみ)
- `src/webtransport/h2/client.py` と `src/webtransport/http2/client.py` の `Client.close` で `self._writer` と `self._reader` を `None` に戻す。`http2.Client.close` は GOAWAY 送出後にピアが先に `close_notify` を送った場合の `wait_closed` の `ssl.SSLError` を握る (`h2.Client.close` と同じ扱い。握らないと `close()` 自体が失敗し、再接続のテストが成立しない)
- 5 層の `Client.connect` の docstring に再入の契約を追記する (transport が残っている間も拒否することを含む)
- `src/webtransport/http3/client.py` の `Client.close` は `await self._send_pending()` が失敗するとソケットを破棄しないため、その場合は transport が残り再入が拒否される。この後始末は別 issue (0211) の修正で解消される前提であり、本 issue では docstring / SKILL.md の「`close()` が完了した後は再度接続できる」という条件表現で区別する
- `close()` の実行中に `connect()` を呼ぶ競合は本 issue の対象外とする (層ごとに挙動が異なる)
- ピアが消滅した後に `Client.is_connected` が True のままになる場合があり (`run()` が接続断で `_connected` を更新しない層がある)、その状態では `close()` を挟まないと再接続できない。`run()` が接続断を観測したときの状態遷移の整理は本 issue の対象外とする (再入ガードが transport の生存も見るため、誤用は拒否される)
- `skills/webtransport-py/SKILL.md` の各層 `Client.connect` の説明に再入の契約を追記する (quic 層の既存記載と揃える)
- `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client.connect` は、キャンセル (`asyncio.wait_for` のタイムアウト等) でも `Client._abandon_attempt` で後始末する (開いた transport を残すと再入ガードに塞がれて再試行できない)
- `src/webtransport/h2/client.py` の内側ハンドラに重複していた後始末は、外側の `except BaseException` へ一本化する
- `tests/test_e2e_connect_reentry.py` を追加し、次の 12 件を検証する
  - 5 層: 接続確立後の再入と `close()` 後の再接続 (quic は `close()` 後も再入を拒否する契約)
  - 5 層: `connect()` 実行中の再入 (応答しないソケットをピアにし、キャンセル後に transport が残らないことも確認する)
  - 4 層: 接続失敗後 (未起動ポート / 接続拒否) に同じインスタンスで再試行して成功すること
  - h2 / http2: `Client._connected` が False でも transport が残っている状態での再入を拒否すること (`close()` の後は再入できる)
