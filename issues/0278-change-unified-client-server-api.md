# HTTPVersion enum で WebTransport の統一 Client / Server を選べるようにする

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/change-api-and-add-qmux
- Polished: {YYYY-MM-DD}

## 目的

WebTransport を使い始める経路を `webtransport.Client(http_version=HTTPVersion.HTTP3)` の 1 つに統一し、`from webtransport import h3, h2` のように「先にプロトコルごとのモジュールを選ぶ」形をやめる。プロトコルの選択がデータ (enum) になると、テストは h2 と h3 を引数で切り替えられるようになり、同じシナリオを両方で回せる。

## 現状

- 高レベル API は `src/webtransport/h3/client.py` / `h3/server.py` と `src/webtransport/h2/client.py` / `h2/server.py` に分かれている
- `h3.Client` と `h2.Client` はメソッドが揃っていない。h3 は `close_stream` / `migrate` / `initiate_key_update`、h2 は `stop_sending` / `on_error` を持ち、`on_goaway` のシグネチャも違う (h3 は `goaway_id`、h2 は `last_stream_id` と `error_code`)
- サーバーのコールバックも形が違う。h3 は `(session_id, stream_id, data, addr)` のように `addr` を引数で受け取り、送信も `addr` をキーにする。h2 は `SessionWriter` を末尾で受け取り、セッション単位で送信する
- テストからの参照は低レベルが中心で、`h2.Session` と `h2.Config` が延べ 278、`h3.Session` と `h3.Config` が延べ 201 に達し、利用ファイルは h2 が 26、h3 が 16 である。高レベルの `h3.Client` / `h3.Server` / `h2.Client` / `h2.Server` を使うテストは 6 ファイルだけである
- `skills/webtransport-py/SKILL.md` と `examples/webtransport/` は `h3.Client` / `h2.Client` を前提に書かれている

## 設計方針

- `src/webtransport/__init__.py` に `HTTPVersion` (`enum.Enum`) を置く。メンバーは `HTTP2` / `HTTP3`、値は ALPN と同じ `"h2"` / `"h3"`、既定は `HTTP3` とする
- `webtransport.Client` / `webtransport.Server` を新設し、`http_version` で実装を選ぶ。クラスはファサードとし、内部に h3 実装または h2 実装を保持する。単一クラスに分岐を並べると、使えない側にもプロトコル固有メソッドが生えてしまうためである
- プロトコル固有 API へは `client.h3` / `client.h2` / `server.h3` / `server.h2` で到達する (選択していない側は `None`)。テストは `migrate()` / `initiate_key_update()` / `close_stream()` / `stop_sending()` / `on_error()` / `on_goaway()` を使うため、既存実装をそのまま下に残す
- `src/webtransport/h3` / `src/webtransport/h2` は Sans-IO 専用のモジュールとして残す。公開するのは h3 が `Session` / `Config` / `Event` / `EventType` / `StreamInfo`、h2 が `Session` / `Config` / `Event` / `EventType` / `WtErrorCode` である。`EventType` のメンバーも `Session` の意味論も層ごとに違うため、統合すると実行時分岐になりテストの記述力が落ちる。低レベル利用は h2 が 26 ファイル、h3 が 16 ファイルに及ぶ
- 高レベルの実装は内部モジュール (`src/webtransport/_h3_client.py` など) へ移し、`webtransport.h3` / `webtransport.h2` からは公開しない
- サーバーのコールバックはセッションハンドルに統一する。`Session` ハンドルが `session_id` / `addr` / `open_stream()` / `send_stream_data()` / `send_datagram()` / `reset_stream()` / `stop_sending()` / `close()` を持ち、h2 の `SessionWriter` を置き換える。h3 は `addr` と `session_id` から同じハンドルを生成する。これで送信のたびに `addr` を渡す非対称が消える
- h2 固有の `on_error` と `SESSION_DRAINING` は、プロトコル固有の観測点として残す
- 統一 API の `connect()` は、層ごとの例外を送出する
- 低レベルの `webtransport.quic` / `http2` / `http3` は変更しない

## 完了条件

- `webtransport.Client(url=..., http_version=HTTPVersion.HTTP2)` と `webtransport.Client(url=..., http_version=HTTPVersion.HTTP3)` の両方で WebTransport セッションを張れる
- `webtransport.Client()` が h3 で動作する (既定値)
- プロトコル固有 API (`migrate` / `initiate_key_update` / `close_stream` / `stop_sending` / `on_error` / `on_goaway`) に統一 API から到達できる
- 同じテストシナリオが `http_version` の値だけを変えて h2 / h3 の両方で動く (少なくとも 1 本は両対応のテストを用意する)
- `examples/webtransport/` が統一 API に追従している
- `skills/webtransport-py/SKILL.md` の API リファレンスが統一 API に追従している
- 全テストが通過する

## 解決方法
