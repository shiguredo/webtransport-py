# QUIC の pacing とストリーム受信フロー制御が大容量転送のスループットを制限する

- Created: 2026-09-13
- Completed: 2026-09-13
- Polished: {YYYY-MM-DD}

## 目的

高レベル API の送信 drain 化 (issue 0153) で 4 MiB 転送は 120 秒超 (1.69 MB で TIMEOUT) から 7.65 秒 (535 KB/s) まで改善したが、32 MiB を 10 秒以内に転送するには足りない。残る律速が `send()` の pacing とストリーム受信フロー制御にあることを特定し、そこを解消する。

## 現状

- `quic.Client` から `quic.Server` へ 1 本の片方向ストリームで 32 MiB を送ると約 99 秒かかる
- `src/bindings/quic.cpp` の `QuicConnection::send` は `ngtcp2_conn_get_send_quantum2` が返す量子ごとにしかパケットを返さない。実測では 1 回の `send()` が返すのは平均 8 パケット程度 (約 9.6 KB) で、pacing が送信機会を細かく刻んでいる
- 送信中の `QuicConnection::max_stream_data_left` が 0 になる回があり、ストリーム受信フロー制御でも止まる。`max_data_left` (コネクション) は 0 にならないため、律速はストリーム単位のウィンドウ
- `QuicConfig` の既定値は `max_data = 1048576` / `max_stream_data_bidi_remote = 262144` / `max_stream_data_uni = 262144`
- 受信側 (`quic.Server`) は `_send_to` を drain 化済みだが、`_receive` は `sock_recvfrom` を 0.1 秒タイムアウトで 1 パケットずつ読む

## 設計方針

- pacing の量子と送信機会の扱いを見直す (`ngtcp2_conn_update_pkt_tx_time` の呼び出し位置と `get_timeout` の関係を確認する)
- 受信側の `_receive` を 1 パケット読みから複数パケット読みに変え、受信処理のまとめ取りで MAX_STREAM_DATA の送出間隔を詰める
- 大容量転送の性能テスト (32 MiB) を追加し、実測値を記録する

## 完了条件

- 32 MiB を 10 秒以内に転送できること
- 大容量転送の性能テストが追加されていること
- 既存のテストが引き続き通過すること

## 関連 issue

- `issues/closed/0153-perf-drain-send-pending-throughput.md` — 送信 drain 化 (本 issue の前提)

## 解決方法

律速は pacing の量子ではなく、受信ループの粒度と固定 sleep だった。送信側は `send()` が `None` を返すまで drain 済みで、`send()` が 1 パケットずつ返すこと自体は律速ではなかった (32 MiB で 23,000 パケットでも送信ループ自体は 0.01 秒で終わる)。実際に効いていたのは次の 2 点で、どちらも受信のたびに遅延が積み上がる構造だった。

- `quic.Server.run` は 1 周につき 1 データグラムだけ読み、末尾で `await asyncio.sleep(0.001)` していた。1 パケット/ms がそのままスループット上限になり、8 MiB で 1.04 MiB/s、32 MiB で 107 秒かかっていた
- `quic.Client._background_recv` は 1 データグラム読みのあと、送信が無いと `await asyncio.sleep(self._wait)` を重ねていた。ngtcp2 は RTT をアプリが `receive()` を呼んだ時刻で計測するため、読むのが遅れると srtt が過大 (実測 24 ms〜859 ms) になり、pacing と PTO も過大になってさらに遅くなる帰還ループに入っていた

対応:

- `src/webtransport/quic/server.py` の `Server.run` を「次の QUIC タイマー期限まで待つ 1 回」+「non-blocking で読めるだけまとめて読む」に変更し、固定 sleep を削除した。待機時間は `Server._timeout_seconds` が全接続の `get_timeout()` の最小値から算出し 0.001〜0.1 秒に clamp する。1 データグラムの処理は `Server._handle_datagram` に切り出し、受信ループから await するコールバックを無くしたまま複数パケットをまとめて取り込めるようにした
- `src/webtransport/quic/client.py` の `Client._receive` を同じまとめ取りに変更し、`_background_recv` の重ね sleep を削除した (受信も送信も無く即座に戻ったときだけ `asyncio.sleep(0)` で他のタスクへ譲る)
- `tests/test_e2e_quic_throughput.py` を追加し、32 MiB の client → server と server → client が 10 秒以内に完了することを検証する

実測 (macOS 26 arm64、loopback、64 KiB チャンク):

| 経路 | 変更前 | 変更後 |
|---|---|---|
| client → server 32 MiB | 107.48 秒 (0.30 MiB/s) | 0.45 秒 (72 MiB/s) |
| server → client 2 MiB / 32 MiB | 11.97 秒 (0.17 MiB/s) | 0.41 秒 (78 MiB/s) |

検証:

- 追加した性能テストが変更前の実装で失敗すること (60 秒待っても完了せず TimeoutError) を確認した
- `uv run pytest tests/ -q --timeout=60` で 1121 件すべて通過することを確認した

残る課題:

- `h3` (WebTransport over HTTP/3) は同じ構造の受信ループを独自に持ち、4 MiB の転送に 63.85 秒 (0.06 MiB/s) かかる。`http3` / `h2` / `http2` も同様に 1 ループ 1 読みと固定 sleep が残る。本 issue の対象外として issue 0208 に切り出した
