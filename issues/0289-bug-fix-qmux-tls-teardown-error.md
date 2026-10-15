# QMux が TLS 終了時の ssl.SSLError を接続断として扱わない

- Created: 2026-10-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-qmux-tls-teardown-error
- Reporter: @voluntas

## 目的

QMux の asyncio 層で、TLS 終了処理中に発生する `ssl.SSLError` を接続断として扱い、公開 API から生の例外が漏れないようにする。

## 現状

- `src/webtransport/qmux/_connection.py` の `_TRANSPORT_ERRORS` は `(ConnectionResetError, BrokenPipeError, ConnectionAbortedError)` のみである。`ConnectionDriver.run()` の `except _TRANSPORT_ERRORS` と、`ConnectionDriver._close_transport()` の `writer.wait_closed()` も同じ組でしか握らない
- TLS 上で QMux を動かしているとき、ローカルが close_notify を送った直後にピアがアプリケーションデータ (CONNECTION_CLOSE など) を送ると `ssl.SSLError` (例: `APPLICATION_DATA_AFTER_CLOSE_NOTIFY`) が発生する。タイミング依存で、macOS では再現せず ubuntu-26.04 の CI で発生した
- 影響 1 (クライアント): `qmux.Client.run()` から `ssl.SSLError` がそのまま抜け、`qmux.Client.close()` からも漏れる。利用者 (moqt-py) は `close()` が `OSError` を握る回避を入れて CI をしのいでいる
- 影響 2 (サーバー): `src/webtransport/qmux/server.py` の `_on_connection` は `except QmuxConnectionError` しか持たないため、接続ハンドラのタスクで未処理例外になる (テストは落ちないがログに残る)
- 影響 3 (ハンドシェイク): `ConnectionDriver.wait_ready()` も `TRANSPORT_ERRORS` を握らないため、ハンドシェイク中の TLS 失敗は生の `ssl.SSLError` が `qmux.Client.connect()` から漏れる。`connect()` の後始末は `except QmuxConnectionError` のみのため `self._driver` が残り、後続の `Client.close()` も `drain()` で同じ `ssl.SSLError` を再送出する
- 影響 4 (close の送信): 接続確立後、`run()` を回していない状態でトランスポートが壊れると、`Client.close()` の `_flush_locked()` (CONNECTION_CLOSE の送信) から `ssl.SSLError` が漏れる。すでに切れた接続を閉じる操作は失敗させない
- `_on_connection` の ALPN 交渉失敗時に `writer.wait_closed()` を握る `except (ConnectionResetError, BrokenPipeError)` にも同じ欠陥がある
- 参考: QMux の `reset_stream` / `stop_sending` の向き違いを `QmuxProtocolError` にする形は `tests/test_qmux_asyncio.py` の `test_direction_mismatch_raises_protocol_error` で固定されている。本 issue はこの形を変更しない

## 再現手順

1. TLS 上で QMux の接続を確立する
2. ピアが close_notify の後に壊れた TLS レコード (アプリケーションデータ型) を送る
3. `qmux.Client.run()` が生の `ssl.SSLError` を送出する、または `qmux.Client.close()` が `ssl.SSLError` を送出する

## 設計方針

- `_TRANSPORT_ERRORS` に `ssl.SSLError` を加える。QMux は TLS 上でも動くため、同時クローズ時の TLS 終了失敗は「接続が切れた」として扱うのが自然である
- `OSError` 全体を握る案は採らない。`ssl.SSLError` は `OSError` のサブクラスであり、TLS 終了失敗だけを名指しできる。無関係な `OSError` (ファイル操作など) まで接続断として扱わない
- `ConnectionDriver.run()` と `_close_transport()` だけでなく、`wait_ready()` も `TRANSPORT_ERRORS` を `QmuxConnectionError` に包む。ハンドシェイク中の接続断も `connect()` の契約 (`QmuxConnectionError`) に揃える
- `ConnectionDriver.close()` は `_flush_locked()` の `TRANSPORT_ERRORS` を許容する。終了処理は best-effort であり、切れた接続へ CONNECTION_CLOSE を書けなくても接続は閉じる (`_close_transport()` と `_closed = True` は必ず通す)。許容する対象は `TRANSPORT_ERRORS` に限定し、他の例外は再送出する
- `qmux.Client.connect()` の失敗時の後始末は、例外の種類によらず `self._driver` を必ず None に戻す (`try` / `finally`)。`self._driver` が残ると `close()` と再 `connect()` が壊れる
- `qmux/server.py` の ALPN 失敗経路の except も同じ組に揃える。定数の二重管理を避ける形 (共有するか、同等のタプルを書くか) は実装時に決める
- 各ストリーム操作 (`send_stream_data` など) の送信時に発生するトランスポート失敗を `QmuxConnectionError` へ包む統一は、本 issue の対象外とする (ライフサイクル経路の漏れだけを扱う)
- テストは実ソケット + 実 TLS のみで検証する。モック・スタブは使わない

## 完了条件

- ピアが TLS 終了中に壊れたレコードを送っても、`qmux.Client.run()` は `QmuxConnectionError` を送出し、`__cause__` に `ssl.SSLError` が残る
- 同じ状況で `qmux.Client.close()` が `ssl.SSLError` を送出しない
- ハンドシェイク中に TLS 失敗が起きても `qmux.Client.connect()` は `QmuxConnectionError` を送出し、その後始末で `self._driver` が残らない (`is_connected` が False になり、`close()` も例外を出さない)
- `run()` を回していない状態でトランスポートが壊れても `qmux.Client.close()` が `ssl.SSLError` を送出しない
- `qmux.Server` の接続ハンドラで未処理例外にならず、既存の警告ログ経路に乗る
- 追加したテストが修正前に失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- QUIC / HTTP/3 層の TLS エラー処理
- `_TRANSPORT_ERRORS` の意味を `OSError` 全体へ広げること
- QMux の `reset_stream` / `stop_sending` のエラー形 (現状の `QmuxProtocolError` を維持する)
