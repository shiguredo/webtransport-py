# WebTransport over HTTP/2 の Client.close がピアの CONNECT ストリームクローズを待たずに接続を閉じる

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-close-waits-peer-fin
- Polished: 2026-09-12

## 目的

draft-ietf-webtrans-http2-15 Section 6.12 は、WT_CLOSE_SESSION を送出した端点に END_STREAM での half-close を MUST、カプセルを受信した端点に END_STREAM で応答してストリームを閉じることを MUST としている。`h2.Client.close` は close_session 送出後に固定の待機ループを回すだけで、ピアの END_STREAM / RST_STREAM を実際には観測していない。h3 側は h3 draft-16 Section 6 末尾の SHOULD (CONNECTION_CLOSE を送る前にピアの CONNECT ストリームクローズを待つ) を受けて `close_wait_timeout` と `_wait_for_peer_close` で実 peer close を観測する実装に置き換え済みであり (closed/0164)、h2 側も同じ観測ベースの待機に揃えて終了応答の観測を best-effort で改善する。closed/0122 が h2 対称対応を本 issue として委譲している。

h2 draft には h3 の当該 SHOULD に対応する規定はなく、h2 には CONNECTION_CLOSE に相当する接続単位のクローズフレームもない。本 issue は仕様要件ではなく、h2 Section 6.12 の MUST 応答の観測と h3 との挙動の対称性を目的とする。

## 現状

- `src/webtransport/h2/client.py` の `Client.close` は `close_session` 送出後に `if not was_running:` の中で `for _ in range(10): await self._receive(); await self._send_pending(); await asyncio.sleep(0.01)` を回すだけで、ピアの END_STREAM / RST_STREAM をイベントとして確認していない
- `was_running` は `self._running` を写したもので、`connect()` 成功時に True が立ったまま run() の起動有無にかかわらず True になる (run タスクを cancel した後や run() を一度も呼んでいない場合も True)。現行の分岐は「run() が実行中か」を表していない
- `h2.Client` のコンストラクタに待機タイムアウトの引数がない
- 対照: `src/webtransport/h3/client.py` は `close_wait_timeout` (既定 3.0 秒) と `_wait_for_peer_close` を持ち、ピアの CONNECT ストリーム終了を `_peer_closed_session_ids` に記録して観測してから接続を閉じる。待機結果は `_close_wait_result` ("peer-closed" / "timeout" / "skipped" / "none") で観測できる
- `H2Session` 側の観測点: ローカル `close_session` 後は `is_terminated` / `is_established = false` が立つため、ピアの END_STREAM では `handle_end_stream` は早期 return する。ピアの END_STREAM / RST_STREAM によるセッション終了は、両ハーフクローズ時の `on_stream_close_callback` が `H2EventType::SessionClosed` を push する経路で観測できる (`tests/test_webtransport_h2_end_stream.py` の `test_initiator_session_closed_after_peer_end_stream_response` が固定済み)
- h2 は TCP 上で動作し、h3 / QUIC の損失検出タイマー (`get_timeout` / `handle_timeout`) に相当する API を持たない

## 設計方針

- `h2.Client` のコンストラクタに `close_wait_timeout: float = 3.0` を追加する (h3 と対称)
- `Client.close` から固定スリープループを撤去し、WT_CLOSE_SESSION 送出後にピアの CONNECT ストリームクローズを `close_wait_timeout` の期限まで観測する。期限までに閉じなければタイムアウトして接続を閉じる
- 観測は `next_event()` の `SESSION_CLOSED` (session_id 一致。RST_STREAM も同じイベント) で行う。run() と close() のイベント消費が競合しないよう、h3 と同じく `_peer_closed_session_ids` に記録し、run() のイベントループでも記録する。待機結果は h3 の `_close_wait_result` と対称の観測点で扱う
- run() の実行中判定は `_running` ではなく専用フラグ (例: `_run_active`) で行う。`run()` の冒頭で True、`finally` で False にし、キャンセル時も確実に落とす。`Client.close` は `self._running = False` の後、`_run_active` が False になるのを deadline 内で待ち、run() 終了後に自身で `_receive` / `_send_pending` / `next_event()` を回して `SESSION_CLOSED` を観測する (run() 終了後は受信者が close() だけになるため `_receive` の同時呼び出しは起きない)
- `connect()` で `_peer_closed_session_ids` を clear し `_close_wait_result` を "none" に初期化する (h3 と対称。再接続で CONNECT ストリーム ID が再利用され得るため、前回の観測を引き継がない)
- 待機中は `asyncio.get_running_loop().time()` の deadline で打ち切り、`_receive` (内部タイムアウト 0.1 秒) と `_send_pending` を回す。h2 に損失検出タイマーはないため、h3 の `get_timeout` / `handle_timeout` 駆動は持ち込まない
- 待機の終了条件は「ピアのクローズ観測」「接続断 (EOF / RST 等)」「タイムアウト」の 3 つとする。接続断時は例外を伝播させずに接続を閉じる処理へ進む。`close_wait_timeout <= 0` では待機をスキップする
- 変更対象: `src/webtransport/h2/client.py` / `tests/test_e2e_webtransport_h2.py` / `skills/webtransport-py/SKILL.md` (h2.Client の引数と close の記述) / `CHANGES.md` の `## develop` への [FIX] エントリ
- 対象外: `src/bindings/webtransport_h2.cpp` / `.h` (既存の `SESSION_CLOSED` イベントで観測可能)、`h2.Server.stop()`、README / docs

## 完了条件

- `h2.Client.close` がピアの CONNECT ストリームクローズ (`SESSION_CLOSED`) を観測してから接続を閉じること (固定スリープループが撤去されていること)
- ピアが期限内にクローズしない場合は `close_wait_timeout` で打ち切って閉じること
- e2e テストで、ピアの close 待機 ("peer-closed" 相当)・タイムアウト ("timeout" 相当)・`close_wait_timeout=0` のスキップ ("skipped" 相当) を検証すること (h3 の `_close_wait_result` と対称の観測点を使う)
- `CHANGES.md` の `## develop` に [FIX] エントリが追加されていること
- 既存のテストがすべて通ること
