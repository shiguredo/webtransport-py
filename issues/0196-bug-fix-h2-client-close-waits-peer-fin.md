# WebTransport over HTTP/2 の Client.close がピアの CONNECT ストリームクローズを待たずに接続を閉じる

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-close-waits-peer-fin
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 3.4 / Section 6.12 は、WT_CLOSE_SESSION 送出後にピアが CONNECT ストリームをクローズするまで待ってから接続を閉じることを求める (SHOULD)。`h2.Client.close` は WT_CLOSE_SESSION 送出後に 10 回 × 10ms の固定スリープループを回すだけで、ピアの END_STREAM / RST_STREAM を実際には観測していない。h3 側は `close_wait_timeout` と `_wait_for_peer_close` で実 peer close を観測する実装に置き換え済みであり、h2 側を対称の設計にする。

## 現状

- `src/webtransport/h2/client.py` の `Client.close` は `close_session` 送出後に `for _ in range(10): await self._receive(); await self._send_pending(); await asyncio.sleep(0.01)` を実行するだけで、ピアの CONNECT ストリームクローズを観測していない
- `h2.Client` のコンストラクタに待機タイムアウトの引数がない
- 対照: `src/webtransport/h3/client.py` は `close_wait_timeout` (既定 3.0 秒) と `_wait_for_peer_close` を持ち、ピアの CONNECT ストリーム終了を観測してから CONNECTION_CLOSE を送出する
- `H2Session` はピアの END_STREAM 受信でセッションエントリを削除し `SessionClosed` イベントを発火する。RST_STREAM 受信時の挙動も `H2Session` 側で観測できる

## 設計方針

- `h2.Client` のコンストラクタに `close_wait_timeout: float = 3.0` を追加する (h3 と対称)
- `Client.close` を、WT_CLOSE_SESSION 送出後にピアの CONNECT ストリームクローズ (END_STREAM / RST_STREAM) を `close_wait_timeout` の期限まで観測する実装に置き換える。固定スリープループは撤去する
- 期限までに閉じなければタイムアウトして接続を閉じる (SHOULD は義務ではないため許容される)
- 待機中も `_receive` / `_send_pending` を回し、損失検出タイマー (`get_timeout` / `handle_timeout`) を駆動する
- h3 側の `_wait_for_peer_close` と命名・構造を揃える

## 完了条件

- `h2.Client.close` がピアの CONNECT ストリームクローズを観測してから接続を閉じること (固定スリープループが撤去されていること)
- ピアが期限内にクローズしない場合は `close_wait_timeout` で打ち切って閉じること
- e2e テストで、ピアの close 待機とタイムアウトの両方を検証すること
- `close_wait_timeout=0` で待機を無効化できること
- 既存のテストがすべて通ること
