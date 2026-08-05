# close() が生成した CONNECTION_CLOSE パケットを送出しない

- Created: 2026-08-05
- Completed: YYYY-MM-DD
- Branch: feature/fix-close-delivers-connection-close
- Polished: {YYYY-MM-DD}

## 目的

`close()` が生成した CONNECTION_CLOSE パケットをピアへ配送できるようにする。現在は生成されたパケットが `send()` から返らず、ピアに接続終了が伝わらない。あわせて ccerr の受信経路・DRAINING 遷移が実質到達不能になっている問題を解消する。

## 現状

- `src/bindings/quic.cpp` の `close` メソッドは `ngtcp2_conn_write_connection_close` の戻り値を無視し、送信バッファに書いた CONNECTION_CLOSE パケットを呼び出し元へ返さない
- `close()` は直後に `closed_ = true` を立てるため、以降の `send()` は `nullopt` を返し、パケットは送出されない
- 結果として:
  - ピアに接続終了 (CONNECTION_CLOSE) が伝わらない
  - ピア側の ccerr (受信した CONNECTION_CLOSE でのみ設定される) が設定されず、コネクションエラー API (error_code / reason) の非 None 経路が到達不能
  - ピア側の DRAINING 遷移 (in_draining_period) が到達不能
  - ハンドシェイク前に `close()` を呼んだ場合、`ngtcp2_conn_write_connection_close` が `NGTCP2_ERR_INVALID_STATE` を返すにもかかわらず `closed_ = true` になるため、`is_closed()` と `in_closing_period` が一致しない

## 設計方針

- `close()` が生成した CONNECTION_CLOSE を次の `send()` が 1 回だけ返す方式にする (Sans-IO 設計と整合)
- `ngtcp2_conn_write_connection_close` が失敗するケース (例: ハンドシェイク前の `NGTCP2_ERR_INVALID_STATE`) の扱いを決める
- 既存の `close()` 関連の挙動 (閉じた後は `send()` が None を返す) との整合を確認する

## 完了条件

- `close()` 後に `send()` が CONNECTION_CLOSE パケットを返すこと
- ピアがその CONNECTION_CLOSE を受信して error_code / reason が設定されること
- ピアが DRAINING 状態 (in_draining_period) になること
- ハンドシェイク前の `close()` の挙動 (is_closed() と in_closing_period の一致) が明確になること
- モックなしのテストで確認する
