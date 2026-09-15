# http3.Server が移行先アドレスからの CLOSED で接続登録を残す

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-server-migrated-closed-address
- Polished: {YYYY-MM-DD}

## 目的

Connection Migration 後のクライアントが移行先アドレスから接続終了を通知した場合、`src/webtransport/http3/server.py` の `Server` は接続登録と DCID 索引を解放できずにリークする。長時間稼働するサーバーで接続の作り直しが繰り返されるとエントリが蓄積する。

## 現状

- `Server._handle_datagram` は DCID 索引で引いた `candidate` について、`quic.ReceiveResult.CLOSED` の分岐で `client = candidate` とするだけにしている。`candidate` は移行前のアドレスをキーに `Server._clients` へ登録されている
- 後段は受信アドレス (`addr`) をそのまま `Server._drain_quic_events` と `Server._process_http3_events` へ渡す。CONNECTION_CLOSED を受けると `Server._remove_client(addr)` が呼ばれるが、`_remove_client` は `self._clients.pop(addr, None)` で引くため何も消えず、旧アドレスのエントリと `Server._dcid_index` の登録が残る
- 同じ状況を他層は処理済みである
  - `src/webtransport/h3/server.py` の `Server._handle_datagram` は `Server._addr_of` で登録済みアドレスを解決し、通知先 (`event_addr`) を旧アドレスに寄せてから後段へ渡す
  - `src/webtransport/quic/server.py` の `Server` は登録済みアドレスを引く経路を持つ
- 移行先アドレスから CLOSED を受けたときに `Server._clients` が空になることを検証するテストが無い

## 設計方針

- `src/webtransport/h3/server.py` と同じ形に揃える。CLOSED 分岐で `Server._addr_of` を使い、以降の処理に渡すアドレスを登録済みアドレスへ解決する
- 解決できない場合 (未登録) は従来どおり受信アドレスを使う

## 完了条件

- 移行先アドレスから CLOSED を受けたとき、`Server._clients` と `Server._dcid_index` から当該接続が除去される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/server.py` の `Server._handle_datagram` の CLOSED 分岐に、`Server._addr_of` による登録済みアドレスの解決を追加し、`Server._drain_quic_events` / `Server._process_http3_events` / `Server._remove_client` へ渡すアドレスを切り替える
- `tests/test_e2e_http3.py` に、移行後にクライアントを切断して `Server._clients` が空になることを確認するテストを追加する
