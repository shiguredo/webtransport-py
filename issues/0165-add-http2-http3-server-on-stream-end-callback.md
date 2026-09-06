# http2.Server と http3.Server にリクエストボディ終端通知の on_stream_end コールバックを追加する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http2-http3-server-on-stream-end-callback
- Polished: {YYYY-MM-DD}

## 目的

`http2.Server` と `http3.Server` は `on_request` (HEADERS) と `on_data` (DATA) のコールバックを持つが、`on_stream_end` に相当するリクエストボディ終端通知が無い。POST リクエストのボディ受信完了を検知できず、examples は HEADERS 受信時点で応答するしかない (`examples/http3/server.py` の実装がそう)。Client 側 (`http2.Client.on_stream_end` / `http3.Client.on_stream_end`) には既にあるため非対称。CODEBASE.md「E2E テスト目的に利用できるよう API を充実させること」にも整合する API 追加。

## 現状

- `src/webtransport/http2/server.py` の `Server._handle_client` のイベント分岐は `HEADERS` / `DATA` / `GO_AWAY` のみ (`STREAM_END` を捨てる)
- `src/webtransport/http3/server.py` の `Server._process_http3_events` は `HEADERS` / `DATA` / `RESET` / `RESET_STREAM` / `STOP_SENDING` のみ (`STREAM_END` を捨てる)
- 対照 (Client 側): `http2/client.py` の `Client.on_stream_end` と `http3/client.py` の `Client.on_stream_end` は既に存在
- Sans-IO 層 (`Http2Connection` / `Http3Connection`) は `StreamEnd` イベントを既に発火している (`http2.cpp` の `Http2Connection::on_frame_recv_callback`、`http3.cpp` の `Http3Connection::end_stream_cb`)
- `examples/http3/server.py` は HEADERS 受信時点で `submit_response` を呼び、POST ボディの受信完了を待たない
- 既存 issue: 0140 「HTTP/2・HTTP/3 の受信トレーラと 1xx を Headers から区別できるようにする」は関連するが本 issue とは目的が異なる

## 設計方針

- `http2.Server` に `on_stream_end(stream_id, response_writer)` (仮) コールバックを追加し、Server の `_handle_client` の `STREAM_END` イベントで発火する
- `http3.Server` に `on_stream_end(stream_id, addr)` (仮) コールバックを追加し、同型で発火する
- コールバックのシグネチャは各層の既存慣例 (writer ベース / addr ベース) に合わせる
- `examples/http3/server.py` を「HEADERS で `on_request`、DATA で `on_data`、STREAM_END で `on_stream_end` を受けて応答」に修正する
- SKILL.md にコールバック一覧を追記する

## 完了条件

- POST リクエストのボディ受信完了が `on_stream_end` で検知できること
- 既存の `on_request` / `on_data` の動作が変わらないこと
- `examples/http3/server.py` が POST 応答を正しく実装できること
- `tests/` に `on_stream_end` の発火を検証するテストを http2 / http3 に追加すること
- 既存のテスト全 822 件が引き続き通過すること
