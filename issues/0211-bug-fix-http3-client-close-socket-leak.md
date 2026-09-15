# http3.Client.close() が送信失敗時にソケットを閉じない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-client-close-socket-leak
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` の `Client.close` は、CONNECTION_CLOSE の送出に失敗するとソケットを閉じないまま例外で抜ける。ファイルディスクリプタが解放されないため、接続の作り直しを繰り返す用途でリークする。

## 現状

- `Client.close` は `self._quic_connection.close()` の後に `await self._send_pending()` を呼び、その後に `self._socket.close()` を呼ぶ。`_send_pending` は `OSError` を送出し得るが `try` / `finally` が無いため、送出されると `self._socket.close()` に到達しない
- 同じ処理を行う他層は後始末が保証されている
  - `src/webtransport/quic/client.py` の `Client.close` は `try` / `finally` と `except OSError` を持ち、失敗を warning に落としてから必ずソケットを閉じる
  - `src/webtransport/h3/client.py` の `Client.close` は `finally` で必ず閉じる
- `close()` の直前にピアが消滅している場合や、ソケット差し替え直後に `close()` した場合に到達しやすい

## 設計方針

- `src/webtransport/quic/client.py` の `Client.close` と同じ形に揃える。送出失敗は warning ログに落とし、ソケットのクローズは `finally` で必ず実行する
- 例外の種類は `OSError` を捕捉する。`_quic_connection.close()` 由来の予期しない例外は従来どおり伝播させ、`finally` でソケットだけは閉じる

## 完了条件

- `Client.close` の送出が `OSError` で失敗しても `self._socket` が閉じられ `None` になる
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.close` の本体を `try` / `except OSError` / `finally` で構成し直す
- `logger` を追加して送出失敗を記録する (`src/webtransport/quic/client.py` と同じ書式)
- 対向を先に落としてから `close()` を呼び、`Client` のソケットが閉じられることを検証するテストを `tests/test_e2e_http3.py` に追加する
