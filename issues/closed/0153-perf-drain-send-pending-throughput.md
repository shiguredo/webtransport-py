# 高レベル API の送信を 1 受信 1 パケットの律速から drain 化してスループットを回復する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/perf-drain-send-pending-throughput
- Polished: {YYYY-MM-DD}

## 目的

UDP 系の高レベル API (`quic.Client` / `quic.Server` / `h3.Client` / `h3.Server` / `http3.Client` / `http3.Server`) は `_send_pending` / `_send_to` で 1 呼び出しあたり `send()` を 1 回しか呼ばない。イベントループは `await self._receive()` の 0.1 秒タイムアウトと `await asyncio.sleep(0.01)` に律速されるため、実効スループットが約 10 パケット / 秒 (実測 14 KB/s @ 4 MiB 転送) に張り付く。cwnd や回線容量に依存せず、`test_large_post_body` (32 KB) が 10 秒 timeout ぎりぎりで通っている状態。1 パケット制約の根拠として書かれたコメント「連続 drain するとストリームデータ滞留時に戻ってこなくなる」は、cwnd 律速 (4 MiB 完走) とフロー制御律速 (`max_stream_data_bidi_remote = 16384` で 1 MiB 完走) の両方で反証済みのため、drain 化して実用スループットを回復する。

## 現状

- 1 パケット制約が入っている場所: `src/webtransport/quic/client.py` の `Client._send_pending`、`src/webtransport/quic/server.py` の `Server._send_to`、`src/webtransport/h3/client.py` の `Client._send_pending`、`src/webtransport/h3/server.py` の `Server._send_to`、`src/webtransport/http3/client.py` の `Client._send_pending`、`src/webtransport/http3/server.py` の `Server._send_to` (計 6 箇所)
- 各所のコメント「send() の連続 drain は ACK 待ちが必要なケースでハングするため 1 パケットに留める」の根拠は不明。CHANGES.md 記載の `[FIX] QUIC の send() が輻輳ウィンドウ枯渇時に無限ループする` / `[FIX] QUIC の send() が ngtcp2 の WRITE_MORE 契約に違反し大容量データ転送でデータが壊れる` で C++ 側は修正済み
- 実験 (scratchpad `drain_flowctl.py`): 4 ストリーム × 256 KiB の同時送信を `max_stream_data_bidi_remote=16384 / max_data=65536` で行っても `send()` を nullopt まで回して 1 MiB 完走 (0.02 秒、19 ラウンド、ハング無し)
- 実験 (scratchpad `exp1_drain.py`): 4 MiB × 1 ストリームを cwnd 枯渇状態で `send()` を nullopt まで回して 4 MiB 完走 (2985 パケット、`pkt_lost=0`)
- `src/webtransport/http3/client.py` の `Client._drain_all` (`_close_on_h3_error` 経由) には既に 64 パケット上限の drain 化された経路がある
- 実測: h3 Client から 1 本の単方向ストリームで 4 MiB を送ると 120 秒で 1.7 MB しか届かず TIMEOUT

## 設計方針

- 6 箇所の `_send_pending` / `_send_to` を「`send()` が nullopt を返すまで drain」に変える。既存の `_drain_all` (`http3/client.py:180-202`) を参考にし、上限を設けるか無制限にするかは実装時に決定する (安全側で 128 パケット上限などの上限を設けても、現状の 1 パケット制約に比べて桁違いに改善する)
- 根拠不明のコメント「連続 drain するとストリームデータ滞留時にハングする」を実装から取り除く
- 修正時の副作用注意点:
  - `ngtcp2_conn_update_pkt_tx_time` を呼ぶ改修 (別 issue 予定) と同時に入れる場合、pacing で `send()` が 0 を返す頻度が上がる。get_timeout が pacing 期限を返すため、Python の `sleep(0.01)` ポーリングとの相互作用を確認する
  - `connect()` 待ちループでの `handle_timeout` 呼び出しの改修 (issue 0152) と順序依存あり
- drain 化に伴う回帰リスクを避けるため、大容量転送 (数十 MiB) の性能テストを追加する

## 完了条件

- h3 Client から 32 MiB 以上のデータを 10 秒以内に転送できること
- `test_large_post_body` (`tests/test_e2e_http2.py` / `test_e2e_http3.py`) が 1 秒未満で通過すること
- `send()` を drain する際に無限ループ・ハングが起きないこと (`send()` は cwnd 枯渇・フロー制御律速のいずれでも必ず nullopt を返すことを実験で実証済み)
- 既存のテスト全 822 件が引き続き通過すること
