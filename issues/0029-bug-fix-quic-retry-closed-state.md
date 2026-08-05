# QUIC サーバーの RETRY 送出要求時に接続が閉じられた状態にならない

- Created: 2026-08-05
- Completed: YYYY-MM-DD
- Branch: feature/fix-quic-retry-closed-state
- Polished: 2026-08-05

## 目的

`ngtcp2_conn_read_pkt` が `NGTCP2_ERR_RETRY` を返した場合に接続が閉じられた状態にならず、`is_closed()` が false のまま残る問題を修正する。`NGTCP2_ERR_RETRY` はサーバー側にのみ返るエラーであり、サーバーが Retry パケットを送出してコネクション状態を破棄すべきことを示す。本ライブラリのサーバーには Retry パケット送出手段が無いため、このエラーを受けた接続は継続不能であり、閉じた状態として扱うのが正しい。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッド内で、`NGTCP2_ERR_RETRY` の分岐は ConnectionClosed イベントを push するだけで `closed_` を立てていない
- 他の終了系エラー (`NGTCP2_ERR_DRAINING` / `NGTCP2_ERR_CLOSING` / `NGTCP2_ERR_DROP_CONN` / `NGTCP2_ERR_CRYPTO`) は `closed_ = true` を立てる
- `NGTCP2_ERR_RETRY` はサーバー側でのみ返り、クライアントアプリケーションには返らない (ngtcp2.h の `ngtcp2_conn_read_pkt` のドキュメント「Client application does not get this error code.」)。クライアントが Retry パケットを受信した場合は ngtcp2 が内部で処理し、トークン付き Initial を再送してハンドシェイクを継続する (RFC 9000 Section 17.2.5.2「The client responds to a Retry packet with an Initial packet that includes the provided Retry token to continue connection establishment.」)。そのため本分岐が対象とするのはサーバー側の受信経路のみ
- サーバーが `NGTCP2_ERR_RETRY` を受けた接続は、ngtcp2 により「Retry パケットの送出とコネクション状態の破棄」を要求される (ngtcp2.h の `ngtcp2_conn_read_pkt` のドキュメント「Server must perform address validation by sending Retry packet... and discard the connection state.」)。しかし本ライブラリには Retry パケット送出の実装が無く、継続不能な接続であるにもかかわらず `closed_` が立たないため、`is_closed()` が false のまま残り、接続統計 API (`ngtcp2_conn_info` 等) が「閉じている場合は None」の契約に反して値を返し続ける
- この不整合が表面化するのは低レベル API (`webtransport.quic.Connection`) の利用時のみ。高レベル API (`src/webtransport/quic/server.py`) では CONNECTION_CLOSED イベントの処理で接続が削除されるため、`is_closed()` の不整合は現れない

## 設計方針

- `NGTCP2_ERR_RETRY` の分岐でも `closed_ = true` を立てる (他の終了系エラーと同じ扱い)
- 既存の ConnectionClosed イベントの push は維持する
- Retry パケット送出 (アドレス検証トークンの生成・送出) の実装は対象外とする。RFC 9000 Section 8.1.2 の「the server can request address validation by sending a Retry packet」が正規の応答だが、本 issue は閉じた状態の不整合修正に絞り、送出実装は機能追加として別 issue の範囲とする
- 0030 (close() の CONNECTION_CLOSE 送出) も同じ `src/bindings/quic.cpp` の `closed_` 周辺を変更対象とするため、実装順序によるマージの競合に注意する (変更箇所は receive と close で分かれており実質競合は小さい)

## 完了条件

- サーバー側の `receive()` が `NGTCP2_ERR_RETRY` を処理した後、`is_closed()` が true を返すこと
- 同状態で接続統計 API (`ngtcp2_conn_info` 等) が None を返すこと
- モックなしのテストでサーバー側の `NGTCP2_ERR_RETRY` 経路を再現して確認する。RETRY が返るのは、サーバーが `NGTCP2_CS_SERVER_INITIAL` の間に CRYPTO オフセットが 0 のまま Initial パケットの CRYPTO フレームがバッファリングされた場合 (分割された ClientHello の順序逆転。0-RTT パケットには CRYPTO フレームが含まれないため単独では条件を満たさない) で、本ライブラリのサーバーはアドレス検証トークンを設定しない (ngtcp2 の既定値) ため到達し得る。再現手段 (ClientHello を 1 つの Initial パケットに収まらないサイズにする等) は実測で特定し、再現が困難な場合はその旨と調査結果を報告する
