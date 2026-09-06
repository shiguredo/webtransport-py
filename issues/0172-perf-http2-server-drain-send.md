# http2.Server の送信が 1 ループ 1 フレームに律速され応答スループットが 302 KB/s に張り付く

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/perf-http2-server-drain-send
- Polished: {YYYY-MM-DD}

## 目的

`http2.Server` の `_handle_client` は `connection.send()` を 1 回だけ呼ぶ。`nghttp2_session_mem_send` は 1 フレームずつ返す契約 (nghttp2.h の説明 3496-3498 行「call this function repeatedly until it returns 0」) だが、単発呼び出しでは 1 フレームしか出さない。実測 4 MiB レスポンスで 13.55 秒 (302 KB/s、約 53 ms / フレーム)。`http2.Client._send_pending` は正しくループ (`while True: data = send(); if not data: return; ...`) しているため、同一パッケージ内で契約理解が食い違う。issue 0153 (UDP 系の drain 化) の HTTP/2 版。

## 現状

- `src/webtransport/http2/server.py` の `Server._handle_client` は `data = connection.send(); if data: writer.write(data); await writer.drain()` を 1 回だけ実行
- 同ファイルの `Server.submit_response` / `Server.send_data` / `ResponseWriter.send_headers` / `ResponseWriter.send_data` も同じく `send()` を 1 回だけ (計 6 箇所)
- 対照: `src/webtransport/http2/client.py` の `Client._send_pending` は `while True: data = self._connection.send(); if not data: return; self._writer.write(data); await self._writer.drain()` で正しく drain
- `_deps/nghttp2/v1.70.0/source/lib/includes/nghttp2/nghttp2.h` の `nghttp2_session_mem_send` doc「This function may not return all serialized data in one invocation. To get all data, call this function repeatedly until it returns 0」
- 実測 (4 MiB レスポンス): 13.55 秒、約 302 KB/s
- 対照: `h2.Server` (`SessionWriter` 経由) は `send_callback` 経由で `send_buffer_` に蓄積するため 1 回の `send()` で全量返す実装で本問題の影響を受けない

## 設計方針

- `Server._handle_client` / `Server.submit_response` / `Server.send_data` / `ResponseWriter.send_headers` / `ResponseWriter.send_data` の 6 箇所を「`send()` が空を返すまで drain」に変える (`http2/client.py` の `_send_pending` を参考)
- 送出フレーム数上限を設けるかは要判断 (現状の `_drain_all` は 64 パケット上限)。バックプレッシャーは `writer.drain()` に任せる
- issue 0153 (UDP 系の drain 化) と同時に、drain の共通ヘルパー化を検討する

## 完了条件

- http2.Server から 4 MiB のレスポンスを 1 秒以内に転送できること
- `tests/test_e2e_http2.py` の大容量レスポンステストが劣化しないこと (現状より速くなる)
- 既存のテスト全 822 件が引き続き通過すること
