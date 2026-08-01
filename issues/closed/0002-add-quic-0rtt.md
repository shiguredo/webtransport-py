# QUIC の 0-RTT による early data 送受信を実装する

- Priority: High
- Created: 2026-07-25
- Completed: 2026-08-01
- Model: Composer
- Branch: feature/add-quic-0rtt
- Polished: 2026-08-01

## 目的

README が掲げる「0-RTT / Session Resumption」を完成させる。QUIC 層で early data としてアプリケーションデータを送受信できるようにする。

## 優先度根拠

- README に記載があるが、early data の送受信が実装されておらず 0-RTT が実質機能しない

## 現状

コミット 6792805（2026-07-26）で Session ticket の取得・復元と 0-RTT の試行フラグまでは実装済みである。ただし、0-RTT パケットでアプリケーションデータを送る手段がなく、0-RTT パケットは実際には送出されない。

- 実装済み: `QuicConfig` の `enable_early_data` / `session_ticket` / `early_transport_params`、Session ticket の export / import、`export_0rtt_transport_params` / `is_early_data_accepted` / `was_early_data_attempted`、`SESSION_TICKET` / `EARLY_DATA_REJECTED` イベント、サーバー側の early data 有効化
- 未実装: early data でのアプリケーションデータ送受信。`src/bindings/quic.cpp` の `open_stream` はハンドシェイク完了前は失敗し、asyncio `quic.Client.connect()` はハンドシェイク完了まで待機するため、early data を送る手段がない。0-RTT パケットが送出されないため、early data によるアプリケーションデータの送受信は発生しない

## 設計方針

- ハンドシェイク完了前でも、早期データとしてストリームデータを送れるようにする
- asyncio 層で `EARLY_DATA_REJECTED` をハンドリングし、拒否時は呼び出し側が再送を判断できるようにする
- WebTransport セッションの 0-RTT 確立は仕様上できない（draft-ietf-webtrans-http3 の制約）ため対象外
- 0-RTT はリプレイ攻撃のリスクがあるため、early data 送信は `session_ticket` を明示的に指定した接続でのみ行う（`enable_early_data` はデフォルト有効のままでよい。ticket を指定しなければ 0-RTT は試行されない）

## 完了条件

- ticket と 0-RTT トランスポートパラメータを使った再接続で、ハンドシェイク完了前に early data としてストリームデータを送信でき、サーバーが自身のハンドシェイク完了前に受信できる
- early data の受理は `is_early_data_accepted()` API、拒否は `EARLY_DATA_REJECTED` イベントで観測できる
- モックなしの e2e（2 回接続）で early data の送受信を検証できる

## 解決方法

- `src/bindings/quic.cpp` の `QuicConnection::open_stream` のハンドシェイク完了前のゲートを、0-RTT を試行するクライアント接続 (`early_data_attempted_`) でのみ緩めた。サーバー側は `early_data_attempted_` が常に false のため挙動は変わらない。根拠は RFC 9001 Section 4.6.1
- `src/bindings/quic.cpp` の `QuicConnection::setup_client_session` で、0-RTT トランスポートパラメータを記憶していない接続では 0-RTT を試行しないようにした (RFC 9000 Section 7.4.1 の MUST)
- `src/webtransport/quic/client.py` の `Client` に `register_early_data` を追加した。`connect()` の前に登録し、接続作成後の最初の送信機会に 0-RTT として送出する。登録ごとに双方向ストリームを 1 本開いて送信する。0-RTT を試行しない接続では送出されず、警告ログを出す
- `src/webtransport/quic/client.py` の `Client` に `on_early_data_rejected` を追加し、asyncio 層で `EARLY_DATA_REJECTED` イベントを処理する。拒否後はストリームを開き直して呼び出し側が再送できる (RFC 9001 Section 4.6.2)
- `tests/test_e2e_quic_advanced.py` に e2e テストを 4 本追加した: early data の受理とエコーバック、破損 ticket による拒否と再送、session_ticket 未指定での非送出、0-RTT トランスポートパラメータ未指定での非試行
