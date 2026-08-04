# nghttp3 の優先度制御 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h3-stream-priority
- Polished: {YYYY-MM-DD}

## 目的

HTTP/3 の RFC 9218 (Extensible Prioritization Scheme) によるストリーム優先度の設定と、クライアントから受信した Priority ヘッダーの解釈を Python から行えるようにする。レスポンスの重要度に応じたスケジューリングが可能になる。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection` は優先度 API を公開しておらず、nghttp3 のデフォルト優先度 (ラウンドロビン) で動作する
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_get_stream_priority2`: ストリームの現在の優先度の取得 (サーバーのみ)
  - `nghttp3_conn_set_client_stream_priority`: クライアント起動ストリームの優先度設定 (サーバーのみ)
  - `nghttp3_conn_set_server_stream_priority`: サーバー起動ストリームの優先度設定 (クライアントのみ)
  - `nghttp3_pri_parse_priority`: RFC 9218 の Priority ヘッダーのパース

## 設計方針

- `Http3Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http3.Http3Connection`)
- 優先度は `nghttp3_pri` 構造体の主要フィールド (urgency / incremental) を公開し、`(urgency: int, incremental: bool)` のタプルまたは専用クラスで受け渡しする
- `set_client_stream_priority` はサーバーのみ、`set_server_stream_priority` はクライアントのみで使用可能な点をドキュメントで明示する
- `pri_parse_priority` は受信ヘッダーから優先度をパースするユーティリティとして公開し、`recv_header_cb` で `:protocol` 以外の Priority ヘッダーを解釈できるようにする
- WebTransport (H3Session) では優先度は使わないため `H3Session` には追加しない
- `src/webtransport/http3.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からストリームの優先度を取得・設定できる
- Python から RFC 9218 の Priority ヘッダー値をパースできる
- モックなしのテストで、優先度の設定とパースが動作することを確認する
