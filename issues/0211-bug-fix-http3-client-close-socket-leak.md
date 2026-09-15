# http3.Client.close() が送信失敗時にソケットを閉じない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-client-close-socket-leak
- Polished: 2026-09-15

## 目的

`src/webtransport/http3/client.py` の `Client.close` は、CONNECTION_CLOSE の送出に失敗するとソケットを閉じないまま例外で抜ける。`Client` 自身が持つソケットの後始末が例外で中断され、ファイルディスクリプタの解放が GC 任せになる (解放はされるが、`Client` を保持し続ける間は開いたままになる)。あわせて `close()` の呼び出し側も例外で後始末を中断される。

## 現状

- `Client.close` は `self._quic_connection.close()` の後に `await self._send_pending()` を呼び、その後に `self._socket.close()` を呼ぶ。`_send_pending` は `OSError` を送出し得るが `try` / `finally` が無いため、送出されると `self._socket.close()` に到達しない
- QUIC 系の他層は後始末が保証されている
  - `src/webtransport/quic/client.py` の `Client.close` は `try` / `finally` と `except OSError` を持ち、失敗を warning に落としてから必ずソケットを閉じる
  - `src/webtransport/h3/client.py` の `Client.close` は `finally` で必ず閉じる
  - `src/webtransport/http2/client.py` の `Client.close` は同じく後始末が保証されていないが、本 issue の対象外とする
- 送出が `OSError` になる代表例は、`close()` の時点でソケットが既に閉じられている場合である。`Client._send_pending` は非接続 UDP ソケットへ `loop.sock_sendto` で送るため、対向が消滅していても送出は成功し、`Client.migrate` の直後も新しいソケットが入っているため成功する

## 設計方針

- `src/webtransport/quic/client.py` の `Client.close` と同じ形に揃える。送出失敗は warning ログに落とし、ソケットのクローズは `finally` で必ず実行する
- 例外の種類は `OSError` を捕捉する。`_quic_connection.close()` 由来の予期しない例外は従来どおり伝播させ、`finally` でソケットだけは閉じる

## 完了条件

- ソケットを先に閉じた状態で `Client.close` を呼び、`await self._send_pending()` が `OSError` で失敗しても `self._socket` が閉じられ `None` になる
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.close` の本体を `try` / `except OSError` / `finally` で構成し直す。`Client._send_pending` の `OSError` は内側の `try` で捕捉して warning に落とし、`_quic_connection.close()` 由来の予期しない例外は伝播させたまま `finally` でソケットを閉じる (`src/webtransport/quic/client.py` の `Client.close` と同じ形)
- `logger` を追加して送出失敗を記録する (`logging.getLogger(__name__)` と `"failed to send connection close: %s"` の書式)
- `tests/test_e2e_http3.py` にテストを追加する。接続確立後に `client._socket` を先に閉じてから `await client.close()` を呼び、`OSError` が伝播せず `client._socket is None` になることを検証する (OSError の発生は `caplog` でも確認する)。`tests/test_e2e_quic.py` の `test_client_close_returns_zero_when_send_fails` が同じ手順の先例である
