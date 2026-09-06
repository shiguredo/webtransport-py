# h3.Client.connect() と http3.Client.connect() の待ちループが QUIC タイマー処理を呼ばず、ハンドシェイクパケット 1 つのロスで永久失敗する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-connect-handle-timeout
- Polished: {YYYY-MM-DD}

## 目的

`h3.Client.connect` は「HANDSHAKE 完了待ち」「SETTINGS 受信待ち」「2xx 応答待ち」の 3 つのループを回すが、いずれも `QuicConnection.get_timeout()` / `handle_timeout()` を呼ばない (`run()` では呼ぶ)。ngtcp2 の loss detection timer が起動されないため、クライアント → サーバー方向のハンドシェイクパケットが 1 つでも失われると再送が発生せず、`connect(timeout=...)` が必ずタイムアウト (deadline 到達) に落ちる。`http3.Client.connect` に至っては timeout 引数も deadline も無く無限待ち。実ネットワークで最初のフライトのロスは頻繁に起きるため、接続確立の信頼性が実用に耐えない。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` の 3 つの待ちループ (ハンドシェイク・SETTINGS・2xx) はいずれも `get_timeout()` / `handle_timeout()` を呼ばない
- 同じ `Client` の `run()` は `handle_timeout()` を呼ぶ経路がある
- `src/webtransport/http3/client.py` の `Client.connect` は `async def connect(self) -> bool` で timeout 引数無し、`while self._running:` のみで打ち切り条件なし → 応答の無い宛先で無限待ち
- 実験 (UDP リレーでクライアント → サーバー方向の最初の 1 パケットを落とす): `h3.Client.connect(timeout=6.0)` が 6.03 秒後に `ConnectTimeoutError`。リレーのカウンタは `c2s=2` で PTO 再送が起きていない
- 対照実験 (サーバー → クライアント方向を 1 パケット落とす): 0.24 秒で回復 (`_deps/ngtcp2/reliable-stream-reset/source/lib/ngtcp2_conn.c` の `conn_recv_pkt` がハンドシェイク未完了時に loss detection timer を自前で駆動するため)
- `http3.Client.connect` は閉塞ポート宛の実験で 5 秒以上ハング
- issues/pending/0054 「ハンドシェイク損失下でも接続が完了することを LossyRelay で検証する」は関連するがテスト追加のみで、このバグ自体は追跡していない

## 設計方針

- `h3.Client.connect` の 3 つの待ちループそれぞれで、`await self._receive()` / `_send_pending()` の間に `timeout = self._quic_connection.get_timeout(); if timeout is not None and timeout <= 0: self._quic_connection.handle_timeout()` を追加する (`run()` の 807-809 行と同じ形)
- `http3.Client.connect` に `timeout: float = 10.0` 引数と deadline 制御を追加する
- `http3.Client.connect` の戻り値と例外契約を h3.Client / h2.Client と揃えるかは別 issue (`0159` 相当) に分離する。本 issue はタイマー駆動の修正に絞る
- issues/pending/0054 の LossyRelay 経由テストで本修正の回帰ピンを取ることを想定する

## 完了条件

- `h3.Client.connect(timeout=6.0)` がクライアント送信 1 パケットのロスから ~0.5 秒以内に回復し接続が完了すること
- `http3.Client.connect(timeout=6.0)` が同条件で回復すること
- 応答の無い宛先で `http3.Client.connect(timeout=6.0)` が 6 秒程度で `ConnectTimeoutError` を送出すること
- `tests/` に「クライアント → サーバー方向のハンドシェイクパケット 1 つのロス下で connect が完了する」テストを h3 / http3 に追加すること
- 既存のテスト全 822 件が引き続き通過すること
