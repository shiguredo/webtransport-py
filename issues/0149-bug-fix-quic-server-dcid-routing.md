# quic.Server が未知アドレスからの short header パケット 1 発で既存接続のアドレスキーを誤って張り替える

- Created: 2026-09-06
- Completed: 2026-09-08
- Branch: feature/fix-quic-server-dcid-routing
- Polished: 2026-09-07

## 目的

`quic.Server.run` は未知アドレスからの short header パケットを既存接続に「試し受信」させ、`receive()` が正の値を返した接続を「所属先」として `_connections` の辞書キーを張り替える。しかし `QuicConnection::receive()` は ngtcp2 が DCID 不一致で破壊したパケット (`NGTCP2_ERR_DISCARD_PKT`) も戻り値 0 を経由して呼び出し側の `data.size()` を返すため、破棄されたパケットでも「所属した」と誤判定する。結果、未知アドレスから任意の short header 1 発を送るだけで、無関係な既存接続のアドレスキーが攻撃者アドレスに張り替わる。接続ハイジャック相当の脆弱性かつ、O(N) 走査による CPU DoS の入口。RFC 9000 Section 5.2 は接続の照合を DCID で行うことを求めており (zero-length CID の例外はあるが、本実装の発行する CID は 8 バイトのため非該当)、現状の設計自体を DCID ルーティングに変える必要がある。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` は未知 `addr` からのパケットで、Short header なら既存の全 `_connections` に対して `existing.receive(data, ...)` を試し、`processed > 0` なら `del self._connections[old_addr]; self._connections[addr] = existing; connection = existing; break`
- `src/bindings/quic.cpp` の `QuicConnection::receive` は `ngtcp2_conn_read_pkt` が 0 を返した場合 `return data.size();`
- `_deps/ngtcp2/reliable-stream-reset/source/lib/ngtcp2_conn.c` の `conn_recv_cpkt` は DCID 不一致・復号失敗などを `NGTCP2_ERR_DISCARD_PKT` に集約し、`if (nread == NGTCP2_ERR_DISCARD_PKT) { ++conn->cstat.pkt_discarded; return 0; }` で 0 を返す
- 実験 (Sans-IO。検証済み): 確立済み接続に対し 41 バイトの乱数 short header (`0x40` + 乱数 40 バイト) を低レベル `QuicConnection.receive` に渡すと戻り値が 41、`pkt_discarded` が 1 増加する
- 実験 (asyncio): 正規クライアント接続後に別 UDP ソケットから `0x40 + 乱数 40 バイト` を 1 発送ると `server._connections` のキーが攻撃者アドレスに置換される
- `QuicConnection::receive` の doc (`src/bindings/quic.h`) は「処理されたバイト数」と説明しているが、実態は「破棄しても全長を返す」で契約と乖離
- h3 / http3 Server には試し受信の機構自体がなく、本脆弱性と同型のコードは存在しない (`h3/server.py`、`http3/server.py` の `Server.run` は未知アドレスを破棄する)。両者に欠けているのは Connection Migration の受付のみであり、issues/0138 が未対応として追跡中である

## 設計方針

- `QuicConnection::receive` の戻り値を「受理 / 破棄 / 終了」を区別する enum (`ReceiveResult { Accepted, Discarded, Closed }` を想定) に変える。API 追加による非破壊的対応は選ばない (CODEBASE.md の積極的破壊的変更の方針に従う)。呼び出し側の修正は戻り値 enum 化に伴う機械的追随のみであり、対象は `quic/server.py` の `Server.run` 内 2 箇所、`h3/server.py` の `Server.run`、`http3/server.py` の `Server.run` である (DCID ルーティング移植は含まず、h3 / http3 分は issues/0138 に委ねる)
- `quic.Server.run` の所属判定を DCID ベースのルーティングに置き換える。short header の `[1:9]` から DCID (8 バイト) を取り出して照合する。8 バイト固定の根拠は初期 SCID 生成の `NGTCP2_MIN_INITIAL_DCIDLEN` (quic.cpp の初期化 3 箇所) と、追加 CID 発行時に初期 SCID 長 (`conn->oscid.datalen`) で要求される ngtcp2 の動作であり、現行の発行経路では揃う。判定規則は次とする。9 バイト未満の短尺パケットと DCID 長 8 バイト未満の照合対象は破棄する (zero-length CID の受信例外を含め、短い DCID 様パケットの誤照合を防ぐ)。DCID 一致かつ受理 (Accepted) の場合のみアドレスキーを張り替える (正当な Migration)。DCID 一致でも破棄 (Discarded) の場合は張り替えない (本脆弱性の核心経路を塞ぐ)。終了 (Closed) の場合は張り替えず既存の終了処理に委ねる。DCID 不一致の short header は破棄する (short header では新規 accept できない)。long header は既存どおり新規 accept 経路に回す
- DCID から Connection を引く別マップ (`_connections` とは別の DCID 索引) を追加する。`_connections` 自体のキー置換は選ばない (送信系 API の addr 指定を維持する最小変更とする)。CID ローテーションに伴い、新規 CID 発行時 (`get_new_connection_id_cb` での登録) と退役時に索引を更新する。退役検知は ngtcp2 の `remove_connection_id` コールバックを quic.cpp 側に新規配線して行う (現状は未配線である)
- h3 / http3 Server への反映は本 issue の対象外とし、issues/0138 に委ねる。0138 の設計方針は quic 層の試し受信フォールバックの移植を謳っており、本 issue の診断と両立しないため、0138 側の設計見直しが別途必要である
- 修正後の `receive` の doc を実態に合わせて更新する

## 完了条件

- 未知アドレスからの任意の short header 1 発で既存接続のアドレスキーが変わらないこと
- Connection Migration (NAT リバインド) は依然として動作すること (受信元アドレスの変化を検知したうえで、DCID が一致する既存接続に振り分ける)
- 未知アドレスからのパケットに対する走査コストが O(N) から O(1) 相当になること (DCID インデックス経由)
- `tests/` に「未知アドレスからの short header 1 発では既存接続が変わらない」「NAT リバインドを模した receive 元アドレス変化で既存接続が保持される」の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

- `QuicConnection::receive` の戻り値を enum (`ReceiveResult`、受理・破棄・終了) に変える。ngtcp2 の受信カウンタ前後差分で受理と破棄を区別する (破棄はカウンタが進まない)。終了系の契約は維持する
- `quic.Server.run` の未知アドレス short header 経路を DCID 索引 (`_dcid_index`) による O(1) 照合に置き換える。DCID は short header の [1:9] の 8 バイト固定で取り出す (RFC 9000 Section 5.2 に従い照合を試みる)。受理時のみ正当な Migration として張り替え、破棄・終了では張り替えない
- 発行 SCID は既存 `scid` プロパティで照会し、接触のたびに索引を最新化する (送信後とタイムアウト後を含む)。8 バイト以外は登録せず安全側に破棄する。退役残りは試し受信で破棄判定のため無害である
- キー張り替えは逆引き辞書で O(1) にし、終了時はイベント drain と登録外しを行う。タイムアウト時の終了も即時通知と除去の対象にする
- 退役検知の remove_connection_id 新規配線は行わず、ngtcp2 の正規集合の照会で代替する (重複状態を持たず、観測挙動は同等になる)
- h3 / http3 Server の追随は不要であった (戻り値を無視しているため互換性に影響しない)
- 破棄時は呼び出し元アドレスによる内部汚染を戻す
- `tests/test_quic_server_routing.py` に張り替え防止・正当 Migration・spray 無接触・タイムアウト通知の回帰テスト、`tests/test_quic_error_handling.py` に重複破棄テストを追加し、既存の戻り値利用を enum に追随させる
- 全 878 件のテストが通過することと、レビュー 4 周で致命的と重要が 0 件であることを確認した
