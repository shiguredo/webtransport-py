# http3.Client.request() がリクエストを終端しない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/change-http3-request-termination
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` の `Client.request` はリクエストヘッダーを送るだけで、リクエストボディのストリームを終端しない。このため `request()` を呼んだだけでは HTTP リクエストが完了せず、対向のサーバーはリクエストの終端を検知できない。GET のようなボディを持たないリクエストでも応答が返らない。

## 現状

- `Client.request` は `Http3Connection.submit_request` を呼んでから `Client._send_pending` を呼ぶだけで、`fin` を送出しない
- `src/bindings/http3.cpp` の `read_data_cb` は、当該ストリームの送信バッファが空の場合に `NGHTTP3_ERR_WOULDBLOCK` を返す。`Http3Connection.submit_request` は空のバッファエントリを作るため、nghttp3 へ EOF が伝わらず END_STREAM が送出されない
- 利用者が明示的に `Client.send_data(stream_id, b"", fin=True)` を呼べば終端できるが、`Client.request` の docstring にも `SKILL.md` の説明にもその手順が無い
- `src/webtransport/http2/client.py` の `Client.request` は `send_data(..., eof=True)` で終端しており、層間で契約が非対称になっている (`CHANGES.md` の `## develop` に HTTP/2 側の変更が記載されている)
- `examples/http3/client.py` は `Client.request("GET", "/")` の後に終端せず、`examples/http3/server.py` は `on_stream_end` でだけ応答するため、この 2 本の組は応答を返せない。`skills/webtransport-py/SKILL.md` は両者を対のサンプルとして案内している

## 設計方針

- `Client.request` に `body` 引数を追加し、`src/webtransport/http2/client.py` の `Client.request` と同じく常に終端する。`body` が `None` のときは空ボディで終端する
- 後方互換は `CODEBASE.md` の「下位互換を維持しないこと」に従い維持しない。`CHANGES.md` に `[CHANGE]` として追記する
- チャンク送信が必要な利用者向けに、低レベル (`Http3Connection.submit_request` と `Client.send_data`) を使う手順が docstring に残っていることを確認する
- 終端しないまま送りたい用途が実在する場合は、`body` に加えて終端を制御する引数を設ける。ただし既定は終端とする

## 完了条件

- `Client.request` が常にリクエストを終端し、`body` を指定した場合はその内容を送ってから終端する
- docstring と `skills/webtransport-py/SKILL.md` の `http3.Client` の説明が新しい契約に一致する
- `examples/http3/client.py` と `examples/http3/server.py` の組が応答を返せるようになる
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.request` に `body` 引数を追加し、`Client.send_data(stream_id, payload, fin=True)` を呼ぶ形に変更する
- `src/webtransport/http2/client.py` の `Client.request` と同じ引数名・既定値に揃える
- `CHANGES.md` の `## develop` に `[CHANGE]` を追記する
- `tests/test_e2e_http3.py` に GET とボディ付きリクエストのテストを追加する
