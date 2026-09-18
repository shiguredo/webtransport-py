# http3.Server が移行先アドレスからの CLOSED で接続登録を残す

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
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

- `src/webtransport/http3/server.py` の `Server._handle_datagram` の CLOSED 分岐で `Server._addr_of(candidate)` により登録済みアドレスを解決し、`Server._drain_quic_events` と `Server._process_http3_events` へ渡すアドレスを切り替える。`Server._remove_client` は `Server._drain_quic_events` と `Server._close_client_connection_on_h3_error` の内部で呼ばれるため、渡すアドレスを変えることで両経路の削除が登録済みアドレスで行われる
- 未登録の送信元アドレスから最初に届くパケットが CONNECTION_CLOSE になる状況を作るテストを `tests/test_e2e_http3.py` に追加する。手順は次のとおりである (`tests/test_quic_server_routing.py` に、移行元ソケットを差し替えて実パケットを送る同種の先例がある)
  - 接続後、サーバーが `Server._clients` へ元アドレスで登録するまで待つ (登録前に進めると、修正前でも空になって空振りする)
  - 移行開始はハンドシェイク確認後でないと失敗するため、事前に 1 往復させる (`tests/test_e2e_http3.py` の `test_connection_migration_continues_request` も同じ前提で書かれている)
  - 移行の開始は `webtransport.quic.Connection.initiate_migration(local_addr, remote_addr) -> bool` を直接呼び、戻り値が True であることを確認する (高レベル `http3.Client` を使う場合は `Client._quic_connection` 経由で呼ぶ)。`initiate_migration` 自体はソケット送出を行わないが、呼び出し直後の `Connection.send()` は移行先パス用のパケット (1200 バイトのパス検証用パケット) を返す。`send()` が `None` を返すことを「送信していない」の根拠にしない
  - 移行開始から CONNECTION_CLOSE 送出までの間に、クライアントのどのソケットからもパケットを送出しない。`Connection.send()` が返したパケットを移行先ソケットから送る操作も行わない。移行先から 1 パケットでもサーバーが受理すると `quic.ReceiveResult.ACCEPTED` の分岐で `Server._clients` のキーが移行先へ張り替わり、未修正の実装でも `Server._clients` と `Server._dcid_index` が空になってテストが通る (回帰テストとして成立しない)
  - 移行開始直後に `Server._clients` のキーが元アドレスのままであることを確認してから、CONNECTION_CLOSE を移行先相当の別ソケットから送出し、`Server._clients` と `Server._dcid_index` が空になることを検証する
- 高レベル `http3.Client.migrate` は `Client._send_pending` で移行先から最初のパケットを送出するため、`Client.migrate` と `Client.close` の組み合わせではこの状況にならない
