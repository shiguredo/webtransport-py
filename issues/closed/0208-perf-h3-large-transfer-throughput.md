# WebTransport over HTTP/3 と HTTP/3 の高レベル API が 1 ループ 1 パケットと固定 sleep で大容量転送を律速する

- Created: 2026-09-13
- Completed: 2026-09-13
- Branch: feature/update-h3-large-transfer-throughput
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/3 でメディアを送るアプリケーションでは、数 MiB〜数十 MiB の転送が現実的な時間で完了する必要がある。ところが `h3` (WebTransport over HTTP/3) の高レベル API は 4 MiB の転送に 60 秒以上かかり、実用にならない。`quic` 側は issue 0206 で解消済みだが、`h3` / `http3` / `h2` / `http2` はそれぞれ独自の受信ループを持ち、同じ律速が残っている。

## 現状

実測 (macOS 26 arm64、loopback、1 本の双方向ストリーム、64 KiB チャンク):

| 経路 | 転送量 | 時間 | 速度 |
|---|---|---|---|
| `quic` client → server (issue 0206 で解消済み) | 32 MiB | 0.45 秒 | 72 MiB/s |
| `h3` (WebTransport over HTTP/3) client → server | 4 MiB | 63.85 秒 | 0.06 MiB/s |
| `h2` (WebTransport over HTTP/2) client → server | 4 MiB | 1.09 秒 | 3.68 MiB/s |

律速の構造 (すべて同じ形):

- `src/webtransport/h3/client.py` の `Client.run` は 1 周につき `_receive()` で 1 データグラムだけ読み、末尾で `await asyncio.sleep(0.01)` する。1 周あたり 10 ms 固定なので 100 パケット/秒が上限になる
- `src/webtransport/h3/server.py` の `Server.run` は同じく 1 データグラム読み + `await asyncio.sleep(0.001)` で、1 パケット/ms が上限になる
- `src/webtransport/http3/client.py` の `Client.run` / `src/webtransport/http3/server.py` の `Server.run` も同じ形 (`asyncio.sleep(0.01)` / `asyncio.sleep(0.001)`)
- `src/webtransport/h2/client.py` / `http2/server.py` は 1 ループ 1 読み + `asyncio.sleep(0.001)` で、3.68 MiB/s に留まる
- 受信したパケットの処理が遅れると、ngtcp2 の RTT 計測 (アプリが `receive()` を呼んだ時刻) が過大になり、pacing と PTO も過大になってさらに遅くなる (issue 0206 で実測: srtt が 24 ms〜859 ms まで振れた)
- ストリーム受信フロー制御の再開放 (MAX_STREAM_DATA) と ACK も同じループで送出されるため、ループが遅いと送信側のウィンドウが閉じたままになる

## 設計方針

- `quic` で効果があった形を各層へ展開する
  - 受信は「次の期限まで待つ 1 回」+「non-blocking で読めるだけまとめて読む」に変える
  - 固定 sleep を廃止し、待機は受信側に任せる (受信も送信も無く即座に戻ったときだけ `asyncio.sleep(0)` で他のタスクへ譲る)
  - 待機時間は次の QUIC タイマー期限から算出し、0.001〜0.1 秒に clamp する
- `h3` / `http3` / `h2` / `http2` の run ループはそれぞれ構造が異なるため、共通化はせず各層で同じ形に揃える (無理な抽象化をしない)
- 大容量転送の性能テストを `tests/` に追加し、実測値と閾値を残す
- 転送量と閾値は CI ランナーでも安定する範囲で決める (32 MiB を 10 秒以内を第一候補とし、実測に対して十分な余裕を取る)

## 完了条件

- `h3` (WebTransport over HTTP/3) の client → server と server → client の 32 MiB 転送が 10 秒以内に完了すること
- `http3` / `h2` / `http2` の大容量転送も同じ方針で固定 sleep と 1 ループ 1 読みが解消していること
- 各経路の性能テストが追加され、律速が戻ると失敗すること (mutation で確認する)
- 既存のテストが引き続き通過すること

## 解決方法

`quic` で効果があった形 (issue 0206) を各層へ展開した。

- `src/webtransport/h3/client.py` の `Client._receive` と `src/webtransport/http3/client.py` の `Client._receive` を、最初の 1 パケットは期限まで待ち、続きは non-blocking で読めるだけ読む形に変更した。`run()` は `_timeout_seconds()` が返す次の QUIC タイマー期限を待ち時間に使い、固定 sleep (`0.01` 秒) を削除した。待機は受信側に任せ、受信も送信も無く即座に戻ったときだけ `asyncio.sleep(0)` で他のタスクへ譲る
- `src/webtransport/h3/server.py` の `Server.run` と `src/webtransport/http3/server.py` の `Server.run` を、1 データグラムの処理を `_handle_datagram` に切り出したうえで「期限まで待つ 1 回 + 読めるだけまとめて読む」に変更し、固定 sleep (`0.001` 秒) を削除した。待ち時間は `_timeout_seconds()` が全クライアントの `get_timeout()` の最小値から算出し 0.001〜0.1 秒に clamp する。per-client ループの `_send_to` はタイマー満了時だけでなく毎周回呼ぶようにした
- `src/webtransport/h2/client.py` / `h2/server.py` / `http2/client.py` / `http2/server.py` の受信ループ末尾の固定 sleep (`0.001` / `0.01` 秒) を `asyncio.sleep(0)` に置き換えた。TCP の受信待ちは `reader.read()` / `read_task` が担っており、固定 sleep は 1 チャンクあたりの遅延を積み上げるだけだった
- 性能テストを追加した (`tests/test_e2e_webtransport_h3_throughput.py` / `tests/test_e2e_webtransport_h2_throughput.py` / `tests/test_e2e_http3_throughput.py`)

副次的に見つかった不具合も修正した。`src/webtransport/h3/server.py` の `Server.run` はタイマーが遠い (idle timeout のみ) 状態では `_send_to` を呼ばないため、`Server.open_stream` が登録失敗時に積む RESET_STREAM が次にパケットが届くまで送出されなかった。per-client ループで毎周回 `_send_to` を呼ぶようにして解消した (既存テスト `test_server_open_stream_invalid_session_id` が受信ループの高速化でタイミングが変わり検出した)。

実測 (macOS 26 arm64、loopback、64 KiB チャンク):

| 経路 | 変更前 | 変更後 |
|---|---|---|
| `h3` client → server | 4 MiB に 63.85 秒 (0.06 MiB/s) | 32 MiB に 0.57 秒 (56 MiB/s) |
| `h3` server → client | 2 MiB に 32.81 秒 (0.06 MiB/s) | 32 MiB に 0.39 秒 (83 MiB/s) |
| `h2` client → server | 32 MiB に 8.67 秒 (3.69 MiB/s) | 32 MiB に 0.86 秒 (37 MiB/s) |
| `quic` client → server (参考) | 32 MiB に 107.48 秒 | 32 MiB に 0.45 秒 |

検証:

- 追加した性能テストがすべて変更前の実装で失敗すること (h3 / http3 は 60 秒待っても完了せず TimeoutError、h2 は 17.2 秒で閾値超過) を確認した
- `uv run pytest tests/ -q --timeout=60` で 1126 件すべて通過することを確認した
