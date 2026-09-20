# http3.Server が移行先アドレスからの CLOSED で接続登録を残す

- Created: 2026-09-15
- Completed: 2026-09-18
- Branch: feature/fix-http3-server-migrated-closed-address
- Polished: 2026-09-18

## 目的

Connection Migration 後、`src/webtransport/http3/server.py` の `Server` は、受理済みの移行先とは別の送信元アドレスから接続終了を通知されると、接続登録と DCID 索引を解放できずにリークする。具体的には、サーバーが未登録の送信元アドレスから最初に受け取るパケットが CONNECTION_CLOSE である場合である (移行の受理前に届いた終了通知、NAT リバインド直後の終了など)。長時間稼働するサーバーで接続の作り直しが繰り返されるとエントリが蓄積する。

## 現状

- `Server._handle_datagram` は `Server._clients` をアドレスで引けなかったときだけ DCID 索引を引き、`quic.ReceiveResult.CLOSED` の分岐では `client = candidate` とするだけにしている
- 登録済みの移行では `quic.ReceiveResult.ACCEPTED` の分岐が `Server._clients` のキーを移行先アドレスへ張り替えるため、この分岐には入らない。リークするのは、`candidate` の登録キーが受信アドレスと異なるまま (最後に受理した移行先アドレスのまま) 受信アドレスで登録を外そうとする経路である
- 後段は受信アドレス (`addr`) をそのまま `Server._drain_quic_events` と `Server._process_http3_events` へ渡す。CONNECTION_CLOSED を受けると `Server._drain_quic_events` から `Server._remove_client(addr)` が呼ばれるが、`_remove_client` は `self._clients.pop(addr, None)` で引くため何も消えず、登録済みアドレスのエントリと `Server._dcid_index` の登録が残る
- 同じ状況を他層は処理済みである
  - `src/webtransport/h3/server.py` の `Server._handle_datagram` は `Server._addr_of` で登録済みアドレスを解決し、通知先 (`event_addr`) を旧アドレスに寄せてから後段へ渡す
  - `src/webtransport/quic/server.py` の `Server` は登録済みアドレスを引く経路を持つ
- 未登録の送信元アドレスから CLOSED を受けたときに `Server._clients` と `Server._dcid_index` が空になることを検証するテストが無い

## 設計方針

- `src/webtransport/h3/server.py` と同じ考え方に揃える。CLOSED 分岐で `Server._addr_of` を使い、`candidate` の登録済みアドレスを解決する
- 解決した登録済みアドレスは `Server._drain_quic_events` と `Server._process_http3_events` の両方へ渡す。`Server._drain_quic_events` は CONNECTION_CLOSED で `Server._remove_client` を呼ぶため、ここが登録済みアドレスでないと解放されない。`Server._process_http3_events` も揃えるのは、`Server._close_client_connection_on_h3_error` の削除と `Server._send_to` のフォールバック宛先を同じアドレスに統一するためである (h3 は CONNECTION_CLOSED で早期 return するため、この 2 つを揃える必要がない点が構造として異なる)
- 解決できない場合 (未登録) は従来どおり受信アドレスを使う

## 完了条件

- 未登録の送信元アドレスから CLOSED を受けたとき、`Server._clients` と `Server._dcid_index` の両方から当該接続が除去される
- 上記を検証するテストが追加され、全テストが通過する
  - 修正前に失敗し修正後に通る回帰テストであることを確認する

## 解決方法

- `src/webtransport/http3/server.py` の `Server._handle_datagram` に通知先アドレス `event_addr` を導入し、CLOSED 分岐で `Server._addr_of(candidate)` により登録済みアドレスを解決して `event_addr` に寄せるようにした。`Server._drain_quic_events` と `Server._process_http3_events` の両方へ `event_addr` を渡すため、`Server._remove_client` が登録キーで呼ばれて接続登録 (`Server._clients`)・DCID 索引 (`Server._dcid_index`)・接続の DCID 集合 (`Server._conn_dcids`) が解放される (`src/webtransport/h3/server.py` の `Server._handle_datagram` と同じ形)。解決できない場合は従来どおり受信アドレスを使う
- `Server._drain_quic_events` と `Server._process_http3_events` の docstring に、`addr` は `Server._clients` の登録キーを渡す契約であることを明記した
- `tests/test_e2e_http3.py` に `test_server_removes_client_on_closed_from_unregistered_address` を追加した。1 往復後にサーバーが元アドレスで登録していることを確認し、低レベルの `Client._quic_connection.initiate_migration` だけを呼んで移行を開始し (`Connection.send()` が返す保留パケットは送出せずに捨てる)、移行が受理されていないことを確認してから、移行先相当の別ソケットで CONNECTION_CLOSE を送る。`Server._clients`・`Server._dcid_index`・`Server._conn_dcids` が空になることを検証する。送るパケットの DCID が同じ接続に解決されることも事前に表明し、前提が崩れたときに意味のある失敗になるようにした
- 修正前実装では `Server._clients` に登録が残ってテストが失敗することを実測で確認した (回帰テストとして機能する)
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 本対応のスコープ外: `Server.stop()` は `Server._clients` のみを clear し `Server._dcid_index` / `Server._conn_dcids` を解放しない (`src/webtransport/h3/server.py` も同様)。`stop()` 後に同じインスタンスを再 `start()` すると古い DCID 索引が残る
