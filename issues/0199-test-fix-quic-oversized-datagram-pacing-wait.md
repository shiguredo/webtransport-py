# QUIC の過大データグラムテストを pacing 期限待ちに対応させる

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-oversized-datagram-pacing-wait
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_quic_oversized_datagram.py` の `test_oversized_datagram_dropped_after_small_one_delivered` が macOS + Python 3.14 環境で失敗し、CI を flaky に落とすのを解消する。2026-09-10 の PR #136 の CI (macos-26_arm64, 3.14) でこのテストが失敗し、再実行で成功する flaky として観測された。

## 現状

- `tests/test_quic_oversized_datagram.py` の `_deliver_datagram` は `client.send()` と `server.send()` を繰り返してデータグラムを配送する独自ループを持つ
- 両方向とも送信が空振りした場合、`_deliver_datagram` の `if not sent: break` で即座にループを打ち切る
- ngtcp2 の pacing が有効な場合、`send()` は pacing 期限まで `ngtcp2_conn_writev_datagram` が `nwrite=0` を返して空振りする
- ハンドシェイク完了直後の送信で pacing 期限が設定されるため、待機せずに打ち切ると保留中のデータグラムが送出されないままテストが終了する
- ローカル環境 (macOS 26 arm64 / Python 3.14.4) では develop でも安定して再現する
- `tests/conftest.py` の `perform_handshake` と `wait_pacing_timeout` はこの待機に対応済みだが、`_deliver_datagram` は独自実装のため対応漏れになっている

## 設計方針

- `_deliver_datagram` の打ち切り判定を conftest の `wait_pacing_timeout` に委ねる。両方向が空振りの場合は `get_timeout()` の期限まで待って再試行し、期限がない場合のみ打ち切る
- 試行上限は conftest の `PUMP_ATTEMPTS` に合わせ、既存の送受信ポンプと同じ粒度にする
- テスト対象のロジック (過大データグラムの破棄) には手を入れない

## 完了条件

- `tests/test_quic_oversized_datagram.py` の 3 テストがローカル (macOS 26 arm64 / Python 3.14.4) で安定して通過すること
- 全テストが通過すること
- CI (macos-26_arm64, 3.14) で通過すること
