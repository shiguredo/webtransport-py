# 高レベル QUIC クライアントに NAT rebinding 用のソケット差し替えを追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-socket-rebind
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

クライアントのローカルアドレス変更 (NAT rebinding。RFC 9000 Section 9.3.1) を再現できるように、高レベル `Client` のソケット差し替え機構を追加する。

## 現状

- sora-quic の `test_ngtcp2_nat_rebinding` は `client._socket = new_socket` でソケットを直接差し替え、その後のデータ転送が継続することを検証する
- webtransport-py の高レベル `Client` は `_socket` と `_local_addr` を別々に保持しており、`_receive` は `_local_addr` を `receive()` に渡す。素の属性代入では `_local_addr` が更新されず、差し替え後も古いローカルアドレスを送り続けるため NAT rebinding が成立しない
- 既存の `migrate()` は `initiate_migration` を呼んでパス検証を伴うため、NAT rebinding (パス検証を伴わない単純な送信パス更新) の意図と異なる

## 設計方針

- `_socket` と `_local_addr` の両方を更新するソケット差し替え API (または `_socket` セッター) を追加する
- 差し替え時に新しいソケットの `getsockname()` で `_local_addr` を更新し、古いソケットは閉じる
- 以降の `receive()` が新しい `_local_addr` を使うようにする (ngtcp2 がクライアント送信パスの更新として NAT rebinding を処理する)

## 完了条件

- ソケットを差し替えた後、新しいローカルポートでデータ転送が継続できる
- 差し替え前に開いた接続上で、差し替え前後のストリーム echo がどちらも成立する
- テストを追加する (差し替え前後の echo)

## 解決方法

(実装時に追記する)