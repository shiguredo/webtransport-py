# close() が生成した CONNECTION_CLOSE パケットを送出しない

- Created: 2026-08-05
- Completed: 2026-08-06
- Branch: feature/fix-close-delivers-connection-close
- Polished: 2026-08-05

## 目的

`close()` が生成した CONNECTION_CLOSE パケットをピアへ配送できるようにする。現在は生成されたパケットが `send()` から返らず、ピアに接続終了が伝わらない。あわせて、ピア側の ccerr の受信経路と DRAINING 遷移が実質到達不能になっている問題を解消する。

## 現状

- `src/bindings/quic.cpp` の `close` メソッドは `ngtcp2_conn_write_connection_close` の戻り値を無視し、送信バッファに書いた CONNECTION_CLOSE パケットを呼び出し元へ返さない
- `close()` は直後に `closed_ = true` を立てるため、以降の `send()` は `nullopt` を返し、パケットは送出されない
- 結果として:
  - ピアに接続終了 (CONNECTION_CLOSE) が伝わらない
  - ピア側の ccerr (受信した CONNECTION_CLOSE でのみ設定される。ngtcp2 の rx.ccerr は `conn_recv_connection_close` でのみ書き込まれる) が設定されず、コネクションエラー API (error_code / reason) の非 None 経路が到達不能
  - ピア側の DRAINING 遷移 (in_draining_period) が到達不能 (RFC 9000 Section 10.2.2「The draining state is entered once an endpoint receives a CONNECTION_CLOSE frame」)。受信側の経路 (`receive` の `NGTCP2_ERR_DRAINING` 分岐) は既に存在するため、修正は送信側のみ
- ハンドシェイク前の `close()` の挙動:
  - クライアントが最初の Initial を送信する前 (ngtcp2 の状態が `NGTCP2_CS_CLIENT_INITIAL`) は、`ngtcp2_conn_write_connection_close` が `NGTCP2_ERR_INVALID_STATE` を返すにもかかわらず `closed_ = true` になるため、`is_closed()` と `in_closing_period()` が一致しない (前者は true になるが、後者は CONNECTION_CLOSE を書き出せた場合のみ true)
  - ハンドシェイク途中 (Initial 交換済み) の `close()` は成功し、鍵の状態により Initial または Handshake パケットで CONNECTION_CLOSE を生成する (この場合 ngtcp2 は error_code を `NGTCP2_APPLICATION_ERROR` に置換し reason を落とす。RFC 9000 Section 10.2.3)
  - サーバーが最初の Initial を受信する前の `close()` は、ngtcp2 のアプリケーションクローズ経路 (`ngtcp2_conn_write_application_close_pkt`) が Initial 鍵未インストールのままパケットを書こうとしてクラッシュし得る (transport クローズ経路には `NGTCP2_ERR_INVALID_STATE` のガードがあるが application 経路には無い。実測で確認する)

## 設計方針

- `close()` は `ngtcp2_conn_write_connection_close` の戻り値 (nwrite) を確認し、成功した場合 (nwrite > 0) は生成されたパケットを保持する。`send()` は未配送の CONNECTION_CLOSE があればそれを 1 回だけ返し、返した後は従来どおり None を返す (Sans-IO 設計と整合)
- `close()` 直後に `closed_ = true` を立てる挙動は維持する (is_closed() の意味論を保つ。`send()` は closed_ のままでも未配送のパケットを返せるようにする)
- `ngtcp2_conn_write_connection_close` が失敗する場合 (例: クライアント Initial 未送信の `NGTCP2_ERR_INVALID_STATE`) は、`closed_ = true` を立ててパケット無しで終了する (現状の挙動を維持)。`is_closed()` と `in_closing_period()` が一致しないのは既知の挙動として `src/bindings/quic.h` の `in_closing_period` のドキュメントに残す (現行コメントは「ハンドシェイク前に close() を呼んだ場合は ngtcp2 が CONNECTION_CLOSE を書けないため」と一般化しており、正確な条件 (Initial 未送信・未受信時) に更新する)
- サーバーが Initial を受信する前の `close()` のクラッシュを回避する (サーバーかつ Initial 未受信の場合は ngtcp2 を呼ばず `closed_ = true` とする等)。クラッシュしないことが実測で確認できた場合は回避不要と判断してよい
- 受信パケットへの CONNECTION_CLOSE 応答の再送は対象外とする (RFC 9000 Section 10.2.1 の「An endpoint in the closing state sends a packet containing a CONNECTION_CLOSE frame in response to any incoming packet」は MUST / SHOULD ではない。closed_ 後の `receive()` がパケットを処理しない現状を維持し、パケットロス時はピア側のタイムアウトで接続が閉じられる)
- `receive()` 内の終了系エラー (NGTCP2_ERR_CRYPTO 等) からの CONNECTION_CLOSE 送出は対象外とする (本 issue は `close()` 経路のみ)
- 既存の close() 関連テスト (tests/test_quic_error_handling.py の test_send_after_close 等。close() 後に send() が None を返すことを「期待される動作」と断言している) は、本修正で期待動作が変わるため更新する
- 高レベル API: クライアント (src/webtransport/quic/client.py の close()) は close() 後に `_send_pending()` を呼ぶため、本修正により自動的に配送される。サーバー (src/webtransport/quic/server.py の stop()) は close() 後に送信処理を回さないため、stop() に CONNECTION_CLOSE の送信処理を追加する
- 0029 (receive の NGTCP2_ERR_RETRY 分岐) も同じ `src/bindings/quic.cpp` の `closed_` 周辺を変更対象とするため、実装順序によるマージの競合に注意する (変更箇所は close / send と receive で分かれており実質競合は小さい)

## 完了条件

- CONNECTION_CLOSE が生成できた場合、`close()` 後に `send()` がそのパケットを 1 回だけ返し、2 回目以降の `send()` は None を返すこと
- 非ゼロの error_code で `close()` した場合、ピアがその CONNECTION_CLOSE を受信して error_code / reason が設定されること。検証はサーバー側の `close()` で行う (サーバーはハンドシェイク完了で確認状態 (HANDSHAKE_CONFIRMED) になり、1-RTT パケットで CONNECTION_CLOSE を送るため error_code が指定値のまま届く)。クライアント側の `close()` は HANDSHAKE_DONE 受信後に呼べば同様に届くが、ハンドシェイク完了直後 (確認前) は Handshake パケットで送られ、ピア (サーバー) はハンドシェイク状態を破棄済みのため破棄される (error_code も APPLICATION_ERROR に置換される)
- ピアが DRAINING 状態 (in_draining_period) になること
- ハンドシェイク前の `close()` の挙動が定まること: クライアント Initial 未送信の `close()` はクラッシュせず `is_closed()` が true になる (パケットは生成されないため in_closing_period() は false のまま)。サーバー Initial 未受信の `close()` もクラッシュしないこと
- モックなしのテストで確認する (低レベル API の Sans-IO テストで、close() → send() が返すパケットをピアの receive() に渡して error_code / reason の設定と DRAINING 遷移を検証する。サーバー停止 (stop()) 時の配送は e2e テストで確認する)

## 解決方法

- `src/bindings/quic.cpp` の `close` メソッドで `ngtcp2_conn_write_connection_close` の戻り値 (nwrite) を確認し、成功した場合 (nwrite > 0) は生成された CONNECTION_CLOSE パケットを `pending_close_packet_` に保持するようにした。`send()` は未配送の CONNECTION_CLOSE があればそれを 1 回だけ返し、返した後は従来どおり None を返す (closed_ が立っていても返せる)
- 生成できない場合 (クライアント Initial 未送信の `NGTCP2_ERR_INVALID_STATE`、サーバー Initial 未受信の送出量上限) はパケット無しで終了する。ハンドシェイク途中の close() では error_code が APPLICATION_ERROR に置換され reason が落ちる挙動をコメントとテストに明記した
- `src/webtransport/quic/server.py` / `src/webtransport/h3/server.py` の `stop()` に CONNECTION_CLOSE の送出処理を追加した。1 接続の送出失敗が残りを中断しないよう接続ごとに例外を隔離し、ソケット close は finally で保証する
- `src/bindings/quic.h` の `in_closing_period` のドキュメントを正確な条件 (Initial 未送信・未受信、ハンドシェイク途中) に更新した
- `tests/test_quic_conn_state.py` に `test_close_delivers_connection_close` / `test_client_close_delivers_connection_close` / `test_close_before_handshake` / `test_close_mid_handshake_replaces_error_code` を追加した。`tests/test_quic_error_handling.py` の close() 関連テストを新仕様 (send() が CONNECTION_CLOSE を 1 回だけ返す) に更新した。`tests/test_e2e_webtransport_h3.py` にサーバー stop() 時の CONNECTION_CLOSE 配送を検証する e2e テストを追加した
- `tests/prop_http2_roundtrip.py` の `prop_http2_custom_headers_roundtrip` が生成する `te` ヘッダーを除外した (RFC 9113 Section 8.2.2 で `te` は値 `trailers` のみ許容されるため)
- `CHANGES.md` の `## develop` セクションに `[FIX]` エントリを追加した
