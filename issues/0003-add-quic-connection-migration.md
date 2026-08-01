# QUIC の実 path 配線と Connection Migration を実装する

- Priority: High
- Created: 2026-07-25
- Completed: 2026-07-26
- Model: Composer
- Branch: feature/add-quic-connection-migration
- Polished: YYYY-MM-DD

## 目的

Sans-I/O の `receive` / `send` に実アドレスを通し、Connection Migration と Path Validation を使えるようにする。現状のダミー `sockaddr` ではマイグレーションが成立しない。

## 優先度根拠

- QUIC の重要な移動透過性機能が未配線
- 証明書検証・0-RTT の次の基盤機能
- Multipath は現行 ngtcp2 に API がないため本 issue の対象外

## 現状

[`src/bindings/quic.cpp`](src/bindings/quic.cpp) の接続作成・`receive`・`send`・`close` がゼロ初期化のダミー path を使う。`get_path_challenge_data` のみ配線済み。`path_validation` と `ngtcp2_conn_initiate_migration` は未使用。

## 設計方針

- `receive(data, local_addr, remote_addr)` に実アドレスを渡す
- `send` が選んだ path のアドレスを呼び出し側に返す
- 後方互換を壊す場合は `CHANGES.md` に `[CHANGE]` で明示する（ダミー固定は残さない）
- `path_validation` コールバックからイベントを出す
- `initiate_migration(local_addr, remote_addr)` を公開する
- asyncio UDP 層で実際の local/remote を渡し、migration 時はソケット操作と連動する

## 完了条件

- 実アドレス付き `receive` / `send` で既存 e2e が通る
- `initiate_migration` 後に Path Validation が成功し、データ通信が継続する
- モックなしの e2e（同一ホスト上の複数 bind 等）がある

## 解決方法

コミット 6792805 で実装した。

- `receive` / `send` / `create_client` / `accept` に実アドレスを必須化する（`src/bindings/quic.cpp`。CHANGES.md に `[CHANGE]` で明示済み）
- `QuicPacket` に送信元 / 送信先アドレスを載せる（`make_packet`）
- `path_validation` コールバックから `PathValidated` / `PathValidationFailed` イベントを送出する
- `initiate_migration(local_addr, remote_addr)` を公開する（`ngtcp2_conn_initiate_migration`）
- asyncio 層に `Client.migrate()` とサーバー側の migration 受付を実装する
- `tests/test_e2e_quic_advanced.py` に migration の e2e テストを追加する
