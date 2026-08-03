# WebTransport over HTTP/3 の送信バッファが ACK 時に解放されないのを修正する

- Created: 2026-08-03
- Completed: YYYY-MM-DD
- Branch: feature/fix-h3-ack-offset
- Polished: {YYYY-MM-DD}

## 目的

h3 層の送信バッファ (`stream_buffers_`) が ACK 受信時に解放されず、接続終了までメモリに残る問題を修正する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::acked_stream_data_cb` は nghttp3 に登録されているが、`nghttp3_conn_add_ack_offset` / `nghttp3_conn_update_ack_offset` の呼び出しが `src/` のどこにも存在しない
- nghttp3 は `acked_data` コールバックを `nghttp3_stream_update_ack_offset` 経由でのみ呼ぶため、ACK offset が通知されない限り `acked_stream_data_cb` は決して発火しない
- そのため、送信バッファのデータは送信後も削除されない (`H3Session::stream_close_cb` も `stream_buffers_` を削除しない)
- `src/bindings/http3.cpp` の `Http3Connection` も同じ構造 (登録のみで発火しない) だが、`Http3Connection::stream_close_cb` が `stream_buffers_` を削除するため影響は限定的。本 issue では対象外とする

## 設計方針

- 送信データを書き出す箇所で `nghttp3_conn_add_write_offset` を呼び出しているのと同じタイミングで、`nghttp3_conn_add_ack_offset` を呼び出して送信データ量を nghttp3 に通知する
- `H3Session::acked_stream_data_cb` は ACK 済みデータ量分を `stream_buffers_` の先頭から消費する実装が既にあるため、バッファが空になった時点でマップのエントリを削除する処理を追加する
- 本 issue は ACK 経路の解放を担当する。リセット・セッション終了経路の解放は 0010 が担当するため、実装順序によるマージの競合に注意する

## 完了条件

- ACK 受信時に `acked_stream_data_cb` が発火し、ACK 済みデータが `stream_buffers_` から削除され、空になったエントリも削除される
- モックなしのテストで検証できる (送信 → ACK 処理 → バッファ解放を確認する)
