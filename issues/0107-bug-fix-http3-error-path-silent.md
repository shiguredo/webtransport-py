# HTTP/3 のプロトコルエラーで run() が無限ハングする問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-21
- Branch: feature/fix-http3-error-path-silent
- Polished: 2026-08-21

## 目的

HTTP/3 の低レベル `Http3Connection::receive_stream_data` / `Http3Connection::get_streams_to_send` が nghttp3 のエラー (`nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` の負値 return) を握りつぶすため、`closed_` も立たず高レベル `webtransport.http3.Client.run()` / `Server.run()` が終了せず、QUIC 側の `idle_timeout_ns` (`Client` の既定 30 秒) までハングし続ける問題を修正する。

## 解決方法

- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `Http3Connection::get_streams_to_send` の nghttp3 負値分岐に `closed_ = true;` を追加した (Http2Connection と対称)
- `src/webtransport/http3/constants.py` を新設し `H3_GENERAL_PROTOCOL_ERROR = 0x0101` を module 定数として定義 (RFC 9114 Section 8.1)。循環 import を避けるため Client / Server 非依存の独立モジュールとして分離。`__init__.py` から再エクスポート
- `src/webtransport/http3/client.py` の `Client.run` メインループに `is_closed()` チェックを追加し、True なら `_close_on_h3_error` ヘルパで `_quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, ...)` + `_drain_all` (防御的上限 64 パケット) + `_running = False` の一連を実行する (RFC 9114 Section 5.3 Immediate Application Closure)
- `src/webtransport/http3/server.py` の `Server.run` メインループの 2 箇所 (受信成功後分岐 + タイマー処理層) に `is_closed()` チェックを追加し、True なら `_close_client_connection_on_h3_error` ヘルパで `_send_to` → `client.quic_connection.close(...)` → `_drain_all_to` (上限 64) → in ガード付き `del self._clients[addr]` を実行する。タイマー処理層のループ変数は `try` 節の `addr` とシャドウさせないため `client_addr` を使用
- `tests/test_e2e_http3.py` に回帰テスト 2 本を追加:
  - `test_http3_client_run_exits_on_client_close`: Client.close() 経由で run() が終了する回帰
  - `test_http3_server_removes_client_on_client_close`: Client 側 close で Server が該当 client を辞書から回収する回帰
- `CHANGES.md` の `## develop` の `[FIX]` セクションに `[FIX] HTTP/3 のプロトコルエラーで run() が QUIC アイドルタイムアウトまで無限ハングする問題を修正する` を追加
- テスト全 724 件パス、`ruff format` / `ruff check` / `ty check` はすべて通過

フレームエラー経路と TimeoutError 分岐経路の直接検証テストは、Python 側から nghttp3 の負値経路を確実に誘発できないため本 issue では追加せず、bindings 側にテスト専用ヘルパを追加する 0132 (`HTTP/3 bindings にテスト専用の closed_ 強制セットヘルパを追加する`) を同 PR 内で先に起票してから本 issue の完了とした
