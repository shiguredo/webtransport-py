# QUIC の send() が ngtcp2 の WRITE_MORE 契約に違反し大容量データ転送でデータが壊れる問題を修正する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/fix-quic-send-write-more
- Polished: YYYY-MM-DD

## 目的

QUIC の `QuicConnection::send()` が ngtcp2 の `NGTCP2_WRITE_STREAM_FLAG_MORE` の契約に違反しており、大きな HTTP/3 データ転送で断続的にデータの欠落・重複 (ペイロード破損) が起きる問題を修正する。

## 現状

- `tests/test_e2e_http3.py` の `test_large_post_body` (32KB の POST ボディを HTTP/3 でエコーするテスト) が macOS の CI (wheel workflow) で断続的に失敗する。失敗内容は「サーバーが受信したデータが送信データより短い/長い (データの欠落・重複)」で、同一コードの CI 実行でも pass と fail が分かれるため断続的なバグである
- `src/bindings/quic.cpp` の `QuicConnection::send()` は、`NGTCP2_WRITE_STREAM_FLAG_MORE` を使用して複数のストリームデータ・データグラムを 1 パケットに coalescing する
- ngtcp2 の契約 (ngtcp2.h の `NGTCP2_WRITE_STREAM_FLAG_MORE` の記述) では、`MORE` 使用後は `ngtcp2_conn_writev_stream` / `ngtcp2_conn_writev_datagram` を呼び続けて正の値 (確定パケット) か 0 が返るまで回し、**それ以外の ngtcp2 API を呼んではならない**。ストリームデータが無くなった場合は `stream_id=-1` でパケットを確定する
- 現行の `send()` は以下の契約違反がある
  - `MORE` 使用後に `ngtcp2_conn_write_pkt` を呼ぶ (禁止されている)
  - ストリームデータ枯渇時に `stream_id=-1` でパケットを確定しない
  - `NGTCP2_ERR_WRITE_MORE` かつ `ndatalen == 0` のとき partial packet を破棄する (消費済みデータは消去済みなのにパケットは送信されない)
  - 呼び出しごとに `path` / `pi` を新規生成する (契約では `WRITE_MORE` 後は同一の `path` / `pi` を渡す必要がある)

## 設計方針

- `QuicConnection::send()` に `more_used` フラグを追加し、`MORE` フラグを設定したかどうかを追跡する
- ストリームデータ・データグラムの書き込み後に `more_used` であれば、`ngtcp2_conn_writev_stream` を `stream_id = -1` で呼んでパケットを確定する
- `ngtcp2_conn_write_pkt` は `MORE` 未使用の場合のみ呼ぶ (ACK のみのパケット生成)
- API のシグネチャは変更しない

## 完了条件

- `test_large_post_body` のデータ欠落・重複が再現しないこと (ループ実行で確認。断続的なため再現率の低下も許容)
- 既存の全テスト (HTTP/3 / WebTransport / QUIC / データグラム) が引き続き通ること