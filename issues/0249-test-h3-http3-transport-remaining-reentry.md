# h3 / http3 の connect() 再入ガードで transport の残存条件が未テスト

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/test-h3-http3-transport-remaining-reentry
- Polished: {YYYY-MM-DD}

## 目的

0218 で `src/webtransport/h3/client.py` と `src/webtransport/http3/client.py` の `Client.connect` に「確立済み・実行中・前回の transport (ソケット) が生存」の 3 条件からなる再入ガードを追加した。しかし `tests/test_e2e_connect_reentry.py` の `test_transport_remaining_rejects_reentry` は h2 / http2 の 2 層しか検証しておらず、h3 / http3 の transport 残存条件 (`Client._socket is not None`) はテストで固定されていない。ガードから `self._socket is not None` を落としても検出できず、再入でソケットを閉じないまま上書きする退行を許す。

## 現状

- `tests/test_e2e_connect_reentry.py` の `test_transport_remaining_rejects_reentry` は `layer` を `["h2", "http2"]` でパラメータ化し、`client._connected = False` を代入して transport (StreamWriter) だけが残った状態を作り、再入が `RuntimeError` になることと `close()` の後に再入できることを検証する
- h3 / http3 の再入ガードは `self._connected or self._connecting or self._socket is not None` である。同じ条件の `self._socket is not None` を検証するテストが無い
- ガードのコメントは「ピアのセッション終了を `run()` が観測すると `_connected` は False になるが transport は残る」と述べており、h3 / http3 ではこの状態遷移を経由して transport 残存条件に到達できる。0218 の 解決方法 は「確立済み・実行中・transport の生存を見る再入ガードを追加する」としており、完了条件の「接続確立後に `close()` を挟まず `connect()` を呼ぶと 5 層すべてで `RuntimeError` になる」は h3 / http3 でも検証されているが、それは `_connected` が True のままの経路である
- `tests/test_e2e_connect_reentry.py` の `test_connect_in_progress_reentry` は h3 / http3 も含むが、検証対象は `_connecting` (実行中フラグ) とキャンセル後の後始末であり、transport 残存条件ではない
- h2 / http2 のテストは `_connected` を直接代入している。h3 / http3 のソケットは UDP であり、同じ代入では「run() がピアの終了を観測した後の状態」を再現できないため、実サーバーとピア側の終了操作で状態遷移を作る必要がある

## 設計方針

- `test_transport_remaining_rejects_reentry` に h3 / http3 のケースを追加する (既存の h2 / http2 のケースはそのまま維持する)
- 実サーバーへ接続した後、ピア側から接続を終了させ、DUT の `Client.run()` がそれを観測して `_connected` が False になるまで待つ (`Client.is_connected` が False になることを期限付きで確認する)。その時点で `Client._socket is not None` であること (transport が残っていること) を表明してから、`connect()` が `RuntimeError` になることを確認する
- ピア側の終了操作は実測で選ぶ。h3 はセッションを終了させる (`h3.Server` を停止するなど)、http3 は QUIC 接続を閉じる (`http3.Server` を停止するなど) 経路を使い、DUT が接続断を観測することを確認する。DUT が観測しない場合は `run()` を明示的に起動して終了を待つ
- `close()` を呼んだ後に再入できること (transport が破棄されること) も同じテストで確認する
- 内部参照 (`Client._connected` / `Client._socket`) は既存テストの慣行に従う。状態遷移そのものは実サーバーと実ソケットで作る
- 追加したテストは、h3 / http3 のガードから `self._socket is not None` を落とすと失敗することを実測で確認する (RED)
- 変更対象: `tests/test_e2e_connect_reentry.py`

## 完了条件

- h3 と http3 のそれぞれで、transport だけが残った状態 (`run()` がピアの終了を観測した後) の `connect()` が `RuntimeError` になることを検証するテストがある
- 同じテストで、その状態から `close()` を呼んだ後に `connect()` が成功する (再入が通る) ことを検証する
- h2 / http2 の既存ケースが引き続き通過する
- 追加したテストはガードの transport 条件を外すと失敗することを実測で確認する (RED)
- 全テストが通過する
