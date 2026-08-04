# ngtcp2 の接続状態・エラー API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-quic-conn-state
- Polished: {YYYY-MM-DD}

## 目的

QUIC コネクションの状態・エラー詳細・ピア情報を Python から取得できるようにし、エラー診断と運用監視を可能にする。現在は接続が閉じた理由や TLS エラーの詳細を取得する手段が無い。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` は `is_closed()` / `is_established()` / `is_handshake_completed()` / `get_connection_id()` のみ公開しており、エラー詳細は取得できない
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用
  - `ngtcp2_conn_get_ccerr2`: コネクションエラー (エラーコードと理由文字列)
  - `ngtcp2_conn_get_tls_error`: TLS エラーコード
  - `ngtcp2_conn_get_tls_alert`: TLS アラート
  - `ngtcp2_conn_get_remote_transport_params2`: ピアのトランスポートパラメータ
  - `ngtcp2_conn_get_local_transport_params2`: ローカルのトランスポートパラメータ
  - `ngtcp2_conn_get_negotiated_version2`: ネゴシエーションされた QUIC バージョン
  - `ngtcp2_conn_get_client_chosen_version2`: クライアントが選択した QUIC バージョン
  - `ngtcp2_conn_in_closing_period`: CLOSING 状態か
  - `ngtcp2_conn_in_draining_period`: DRAINING 状態か
  - `ngtcp2_conn_get_scid2`: 送信元接続 ID (SCID)
  - `ngtcp2_conn_get_active_dcid3`: アクティブな宛先接続 ID (DCID)

## 設計方針

- `QuicConnection` に状態・エラー取得メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)
- コネクションエラーはエラーコード (int) と理由 (str) を返す。TLS エラーはコードとアラートを返す
- トランスポートパラメータは主要フィールドのみを公開する (全フィールドの公開は行わず、必要なものを選ぶ)
- ハンドシェイク前に取得できない値は `None` を返す
- `src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python からコネクションエラーのコードと理由が取得できる
- Python から TLS エラーとアラートが取得できる
- Python からリモート / ローカルのトランスポートパラメータ (主要フィールド) が取得できる
- Python からネゴシエーションされた QUIC バージョン・SCID・アクティブ DCID・CLOSING / DRAINING 状態が取得できる
- モックなしのテストで、ハンドシェイク後とエラー発生時に値が取得できることを確認する
