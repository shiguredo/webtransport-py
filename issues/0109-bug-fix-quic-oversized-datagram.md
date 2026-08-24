# QUIC の過大データグラムが送信キューを永久に塞ぐ問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-oversized-datagram
- Polished: 2026-08-24

## 目的

RFC 9221 Section 3 の「An endpoint MUST NOT send DATAGRAM frames that are larger than the max_datagram_frame_size value it has received from its peer」に反し、過大なデータグラムを送信キューに積むと後続の全データグラムが永久に送出されない問題を修正する。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::send_datagram` はサイズ検証なしでキューに push する (ガードは接続状態・datagram 有効のみ)
- `ngtcp2_conn_writev_datagram` はピアの max_datagram_frame_size 超過で `NGTCP2_ERR_INVALID_ARGUMENT` を返す。書き出しループは負値で break するため、過大データグラムがキュー先頭に残り続け、以降の全データグラムが送出されない (head-of-line ブロック)
- ピアの transport parameter 未受信 (ハンドシェイク前) や TP 欠落 (max_datagram_frame_size = 0) でも ngtcp2 は `NGTCP2_ERR_INVALID_STATE` を返すため、同じくキューを塞ぐ
- ハンドシェイク前にキューされたデータグラムは、エンキュー時点ではピアの上限を検査できない (optimistic 送信の経路が存在する)

## 設計方針

- **サイズ検査の単位**: ngtcp2 の比較対象は DATAGRAM フレーム全体 (type (1 バイト) + varint(データ長) + データ長) であるため、`1 + varint_len(data.size()) + data.size() > remote_max_datagram_frame_size()` で判定する。データ長のみの比較では境界ケースで ngtcp2 が still エラーを返す
- **破棄の扱い**: 上限超過のデータグラムは `send_datagram` で黙って破棄する (戻り値は void のまま。既存の send_datagram の終了後セッションガードと同じ「黙って無視」の位置づけ。エラー通知は API 変更を伴うため採用しない)
- **ハンドシェイク前・TP 欠落時の自衛**: エンキュー時検査では防げないため、書き出しループで `ngtcp2_conn_writev_datagram` が `NGTCP2_ERR_INVALID_ARGUMENT` / `NGTCP2_ERR_INVALID_STATE` を返した場合、キュー先頭エントリ (現在処理中のデータグラム) を pop して破棄し、ループを継続する (head-of-line ブロックの根絶)
- 変更対象: `src/bindings/quic.cpp` (send_datagram のサイズ検査 / 書き出しループのエラー時 pop) / テスト / CHANGES.md (## develop への [FIX])

## 完了条件

- ピア上限を超えるデータグラムがワイヤへ送出されない (破棄される)
- 過大データグラムをエンキューしても、後続のデータグラムが送出される (ハンドシェイク前・ハンドシェイク後のどちらでも)
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加され、全テストが通る
