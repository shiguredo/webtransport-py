# http3.Client.close() が送信失敗時にソケットを閉じない

- Created: 2026-09-15
- Completed: 2026-09-18
- Branch: feature/fix-http3-client-close-socket-leak
- Polished: 2026-09-18

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

- 接続確立後に `client._socket.close()` でソケットオブジェクトだけを閉じ (`client._socket` 属性は `None` にしない)、`await client.close()` を呼ぶと、`Client._send_pending` の送出失敗が warning ログに落ちて `OSError` が伝播せず、`client._socket` が閉じられ `None` になる
  - `client._socket = None` にすると `Client._send_pending` 冒頭の `if self._socket is None: return 0` で早期 return し、送出を試みないまま `close()` が完了する。この手順では未修正の実装でも `self._socket is None` が成立してしまうため、ソケットオブジェクトを閉じる操作で `OSError` を発生させること
- 送出失敗の warning ログ (`"failed to send connection close: %s"`) が `webtransport.http3.client` ロガーへ出力されることを `caplog` で検証する
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.close` を `try` / `except OSError` / `finally` で構成し直した。`Client._send_pending` の `OSError` は内側の `try` で捕捉して warning に落とし、OSError 以外の例外 (await 中のキャンセル等) と `_quic_connection.close()` の例外は伝播させたまま `finally` でソケットを閉じる (`src/webtransport/quic/client.py` の `Client.close` と同じ形)
- `logging` を import し `logger = logging.getLogger(__name__)` を追加して、送出失敗を `"failed to send connection close: %s"` で記録する (`webtransport.http3.client` ロガー。書式は quic 層と同一)
- `tests/test_e2e_http3.py` の `test_client_close_closes_socket_when_send_fails` を追加した。接続確立後に `client._socket.close()` でソケットオブジェクトだけを閉じ (`client._socket` 属性は残す)、`await client.close()` を呼んで、例外が伝播しないこと、`webtransport.http3.client` ロガーへ `WARNING` で `"failed to send connection close"` が記録されること、`client._socket` が `None` になることを検証する。docstring には検出限界 (OSError 経路のみを通るため、非 OSError 例外で finally が走ることは検証しない) を明記した
- 修正前実装では `close()` から `OSError` が伝播してソケットの参照が残ることを実測で確認した (追加テストが回帰を検出する)
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 本対応のスコープ外: `src/webtransport/http2/client.py` の `Client.close` は同じく後始末が保証されていない (issue の現状で対象外と明記)。`src/webtransport/http3/client.py` の `Client._connect_one` はキャンセル時にソケットを残す経路がある。いずれも別途の対応とする
