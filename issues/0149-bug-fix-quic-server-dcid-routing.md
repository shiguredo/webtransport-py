# quic.Server が未知アドレスからの short header パケット 1 発で既存接続のアドレスキーを誤って張り替える

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-server-dcid-routing
- Polished: {YYYY-MM-DD}

## 目的

`quic.Server.run` は未知アドレスからの short header パケットを既存接続に「試し受信」させ、`receive()` が正の値を返した接続を「所属先」として `_connections` の辞書キーを張り替える。しかし `QuicConnection::receive()` は ngtcp2 が DCID 不一致で破棄したパケット (`NGTCP2_ERR_DISCARD_PKT`) も戻り値 0 を経由して呼び出し側の `data.size()` を返すため、破棄されたパケットでも「所属した」と誤判定する。結果、未知アドレスから任意の short header 1 発を送るだけで、無関係な既存接続のアドレスキーが攻撃者アドレスに張り替わる。接続ハイジャック相当の脆弱性かつ、O(N) 走査による CPU DoS の入口。RFC 9000 Section 5.2 は接続の照合を DCID で行うことを求めており、現状の設計自体を DCID ルーティングに変える必要がある。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` は未知 `addr` からのパケットで、Short header なら既存の全 `_connections` に対して `existing.receive(data, ...)` を試し、`processed > 0` なら `del self._connections[old_addr]; self._connections[addr] = existing; connection = existing; break`
- `src/bindings/quic.cpp` の `QuicConnection::receive` は `ngtcp2_conn_read_pkt` が 0 を返した場合 `return data.size();`
- `_deps/ngtcp2/reliable-stream-reset/source/lib/ngtcp2_conn.c` の `conn_recv_cpkt` は DCID 不一致・復号失敗などを `NGTCP2_ERR_DISCARD_PKT` に集約し、`if (nread == NGTCP2_ERR_DISCARD_PKT) { ++conn->cstat.pkt_discarded; return 0; }` で 0 を返す
- 実験 (Sans-IO): 41 バイトの乱数 short header を未知アドレスから `server.receive` に渡すと戻り値が 41、`pkt_discarded` が 1 増加
- 実験 (asyncio): 正規クライアント接続後に別 UDP ソケットから `0x40 + 乱数 40 バイト` を 1 発送ると `server._connections` のキーが攻撃者アドレスに置換される
- `QuicConnection::receive` の doc (`src/bindings/quic.h`) は「処理されたバイト数」と説明しているが、実態は「破棄しても全長を返す」で契約と乖離
- 同型の非対応が h3 / http3 Server にもある (`h3/server.py`、`http3/server.py`)。issues/0138 が Connection Migration 未対応として追跡中

## 設計方針

- `QuicConnection::receive` の戻り値を「受理 / 破棄 / 終了」を区別する enum に変える (例: `ReceiveResult { Accepted, Discarded, Closed }`)。またはバイト数と受理判定を明確に分離する API を追加する
- `quic.Server.run` の所属判定を、DCID ベースのルーティングに置き換える。short header の先頭バイト以降から DCID を取り出して照合する (自サーバーの SCID 長は 8 バイト固定のため取り出しは決定的)
- `_connections` の辞書キーを DCID (もしくは DCID → Connection の別マップを追加) に変更する
- Connection Migration の観点で h3 / http3 Server にも同型の修正を反映する (issues/0138 と整合)
- 修正後の `receive` の doc を実態に合わせて更新する

## 完了条件

- 未知アドレスからの任意の short header 1 発で既存接続のアドレスキーが変わらないこと
- Connection Migration (NAT リバインド) は依然として動作すること (受信元アドレスの変化を検知したうえで、DCID が一致する既存接続に振り分ける)
- 未知アドレスからのパケットに対する走査コストが O(N) から O(1) 相当になること (DCID インデックス経由)
- `tests/` に「未知アドレスからの short header 1 発では既存接続が変わらない」「NAT リバインドを模した receive 元アドレス変化で既存接続が保持される」の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
