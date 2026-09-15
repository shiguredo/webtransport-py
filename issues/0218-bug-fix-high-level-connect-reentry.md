# 高レベル層の connect() 再入でソケットと Writer が漏れる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-high-level-connect-reentry
- Polished: {YYYY-MM-DD}

## 目的

`close()` を呼ばずに `connect()` を再度呼ぶと、前回のソケットまたは `StreamWriter` を閉じないまま上書きする。ファイルディスクリプタと受信タスクが解放されないため、接続の作り直しを繰り返す用途でリークする。

## 現状

- `src/webtransport/quic/client.py` の `Client.connect` は `self._recv_task` と `self._connecting` を見て `RuntimeError("connect() has already been called")` を送出する。唯一の再入ガードである
- 他の 4 層にはガードが無く、前回の状態を閉じずに上書きする
  - `src/webtransport/h3/client.py` の `Client.connect` は `Client._attempt_connect` 経由で `self._socket = socket.socket(...)` を再代入する。前回のソケットは閉じられない
  - `src/webtransport/http3/client.py` の `Client.connect` も同様に `self._socket` を再代入する
  - `src/webtransport/h2/client.py` の `Client.connect` は `self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(...), ...)` で `StreamWriter` を上書きする。前回の `StreamWriter` は閉じられない
  - `src/webtransport/http2/client.py` の `Client.connect` も `self._reader, self._writer = await asyncio.open_connection(...)` で上書きする
- いずれの層も「接続済みのインスタンスへ再び `connect()` を呼ぶ」ことを禁じる記載が docstring に無い

## 設計方針

- `src/webtransport/quic/client.py` と同じ形の再入ガードを他の 4 層にも入れ、`RuntimeError` で拒否する。誤用を黙って受け入れて前回のリソースを漏らすより、明示的に拒否する方が安全である
- ガードの判定に使う状態は層ごとに異なる
  - h3 / http3 は `self._socket` が `None` でないこと、または受信タスクの有無
  - h2 / http2 は `self._writer` が `None` でないこと
- ガードを入れる代わりに「再入時は前回を閉じてから接続し直す」という設計もあり得るが、`quic` の既存契約と揃える方を採用する
- docstring に「`close()` の前に再度 `connect()` を呼ぶと `RuntimeError` になる」旨を明記する

## 完了条件

- 5 層すべてで `close()` 前の `connect()` 再呼び出しが `RuntimeError` になる
- 各層の docstring に再入の契約が記載される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/h3/client.py` / `http3/client.py` / `h2/client.py` / `http2/client.py` の `Client.connect` の冒頭に再入ガードを追加する
- `tests/` に対象層ごとの再入テストを追加する (`tests/test_e2e_quic.py` の相当テストを参考にする)
