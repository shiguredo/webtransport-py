# セッション終了後に send_datagram がデータグラムを送出してしまう

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-datagram-after-session-close
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 の MUST「セッション終了を学習したエンドポイントは、新しいデータグラムを送信してはならない (it MUST NOT send any new datagrams or open any new streams)」を満たすため、セッション終了後の `send_datagram` を無視する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::send_datagram` は Quarter Stream ID を varint でエンコードして `pending_datagrams_` に直接積むだけで、セッション ID の正当性 (`session_ids_` のメンバーシップ) を確認しない
- セッション終了後 (CONNECT ストリームのクローズ後) にアプリがそのセッション ID で `send_datagram` を呼ぶと、データグラムがそのまま送出される
- 対照的に `open_stream` は `nghttp3_conn_open_wt_data_stream` が終了済みセッションで NGHTTP3_ERR_INVALID_ARGUMENT を返すため防がれる。データグラム送信だけが MUST 違反をライブラリ自身が誘発し得る穴になっている

## 設計方針

- `send_datagram` の冒頭で `session_ids_` のメンバーシップを確認し、終了したセッション ID への送信は黙って無視する (`open_stream` と対称の扱い)
- コードコメントに Section 6 の MUST 文面を引用して根拠を明記する

## 完了条件

- セッション終了後に `send_datagram` を呼んでもデータグラムが送出されない (`get_datagrams_to_send` に現れない)
- 生存セッションへの `send_datagram` は従来どおり送出される
- モックなしのテストで検証できる (Sans-IO 構成)
