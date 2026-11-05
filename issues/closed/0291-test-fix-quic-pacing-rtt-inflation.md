# CI で flaky に失敗する pacing テストの RTT 標本を決定的にする

- Created: 2026-11-05
- Completed: 2026-11-05
- Branch: feature/test-fix-quic-pacing-rtt-inflation
- Polished: {YYYY-MM-DD}

## 目的

2026-11-05 の wheel ワークフロー (ubuntu-24.04 / x86_64 / CPython 3.14) で
`tests/test_quic_pacing.py::test_confirmed_write_sets_pacing_deadline` だけが失敗した。
再実行すれば通る類の失敗であり、CI の信頼性を損なう。テストが RTT 標本を膨らませる
方法を決定的にして、同じ失敗を繰り返さないようにする。

## 現状

- 失敗箇所は `_prepare_and_stall` の `assert timeout is not None`。接続を作り直す
  3 回の試行すべてで pacing 停滞を捉えられなかった
- テストは pacing 間隔が平滑化 RTT に比例することを利用し、送信と配送の間に
  `time.sleep(0.1)` を挟んで RTT 標本を膨らませている。pacing 間隔が send() の
  呼び出し overhead より十分大きければ、輻輳ウィンドウに空きがあるのに送出が
  止まり、期限を観測できる
- RTT 標本は、サーバーが生成した ACK がクライアントに届いた時点で、その ACK の
  largest acked パケットの送信時刻から計算される。現行実装は、パケットを配送した
  次のポンプでサーバーが ACK を送出することを前提にしている
- しかしサーバーの ACK は、遅延 ACK や PMTUD probe の送出に挟まれると、クライアント
  が次に送ったデータパケットを受信した後の ACK になる。その場合 largest acked は
  新しいパケットになり、0.1 秒の標本は失われる
- 標本が失われると平滑化 RTT は handshake の高速標本 (数百 µs) のままになり、
  pacing 間隔は数 µs になる。これは 2 回の send() 呼び出しの間隔より短いため、
  send() は pacing で止まらず輻輳ウィンドウが埋まり、期限を観測できなくなる
- ローカルでは再現しにくい。既存実装のまま 60 回連続で準備すると、平滑化 RTT は
  12.6 ms 前後で安定し、停滞も毎回捉えられた。サンプルが失われた場合だけ
  平滑化 RTT が 0.2 ms 前後になる
- 失敗したジョブではテスト全体が 0.64 秒で終わっており、pacing 待ちが発生して
  いない。標本が失われた状態と一致する
- 2026-09-10 時点の集計でも 100 run 中 3 回、このテストが失敗している
  (0200 の修正で待機上限と閾値は緩和したが、標本の膨らませ方の脆さは残っていた)

## 設計方針

- ライブラリ側は変更しない。ngtcp2 の契約どおり実装されており、脆いのはテストの
  前提である
- RTT 標本は、ACK の生成ではなくクライアントへの配送をテスト側で遅らせて
  膨らませる。手順は次のとおり
  - クライアントの ack-eliciting なパケットを 1 つだけサーバーへ配送する
  - サーバーに ACK を生成させ、テスト側で `_ACK_HOLD_DELAY` 秒保持してから
    クライアントへ配送する
  - 他にパケットを配送しないため、ACK の largest acked は必ず配送したパケットに
    なり、標本は保持時間以上になる
  - サーバーの ACK 生成を遅らせるとサーバーが申告する ACK delay も膨らみ、
    ngtcp2 は標本から ACK delay を差し引くため膨らまない。生成は即座に済ませ、
    配送だけを遅らせる
- ack-eliciting なパケットは、送信後に `bytes_in_flight` が増えることで判別する。
  ACK のみのパケットは in-flight に数えられず、受け取っても ACK が返らない
- 標本が実際に膨らんだことを `latest_rtt` で検証し、膨らまなければ早期に失敗
  させる。黙って高速標本のまま進ませない
- 変更対象: `tests/test_quic_pacing.py`

## 完了条件

- `tests/test_quic_pacing.py` の 2 テストが、CPU 負荷をかけた状態でも 40 回連続で
  通過する
- 全テストが通過する
- CI が緑に戻る

## 解決方法

- `tests/test_quic_pacing.py` から、送信と配送の間に `time.sleep` を挟んで RTT 標本を
  膨らませる処理を削除した
- `_deliver_ack_eliciting_packet` を追加した。送信後に `bytes_in_flight` が増える
  パケットを 1 つだけサーバーへ配送し、ACK のみのパケットを除外する
- `_inflate_rtt` を追加した。サーバーに ACK を生成させたあと `_ACK_HOLD_DELAY`
  (0.1 秒) 保持してからクライアントへ配送する。他のパケットを配送しないため ACK の
  largest acked は配送したパケットになり、標本は保持時間以上になる
- 標本が膨らんだことを `latest_rtt` で検証し、膨らまなければ `AssertionError` に
  する。平滑化 RTT が高速標本のまま進むことを防ぐ
- 定数を `_ACK_HOLD_DELAY` / `_RTT_INFLATE_ROUNDS` / `_PREPARE_PUMP_PACKETS` に整理し、
  不要になった `_TRICKLE_DELAY` / `_TRICKLE_ROUNDS` / `_TRICKLE_PACKETS` を削除した
- 検証: 4 コア相当の CPU 負荷をかけた状態で `tests/test_quic_pacing.py` を 40 回
  連続実行して失敗しないこと、準備後の平滑化 RTT が 23.6 ms 前後で安定することを
  確認した。全テスト (1566 passed, 1 skipped) も通過した
