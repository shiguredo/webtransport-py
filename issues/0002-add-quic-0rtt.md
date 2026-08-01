# QUIC の 0-RTT による early data 送受信を実装する

- Priority: High
- Created: 2026-07-25
- Completed: YYYY-MM-DD
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

- `open_stream` のハンドシェイク完了前のゲートを、0-RTT を試行するクライアント接続でのみ緩める（サーバー側の挙動は変えない）
- asyncio `quic.Client` に、ハンドシェイク完了前に送る早期データを登録できる窓口を追加する（`connect()` の呼び出し前に登録し、接続作成後の最初の送信機会に 0-RTT として送出する方式を想定）
- asyncio 層で `EARLY_DATA_REJECTED` イベントを処理する
- early data の送受信を検証する再接続 e2e を追加する（同一サーバープロセスに対して 2 回接続し、ticket の復号が可能な状態で行う。拒否の観測は ticket のペイロード部を改変した再接続で `EARLY_DATA_REJECTED` を確認する）
