# WebTransport over HTTP/2 で同じストリームへの stop_sending が WT_STOP_SENDING を複数回送出する

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stop-sending-duplicate-send
- Polished: 2026-09-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.3 は「A WT_STOP_SENDING capsule MUST NOT be sent multiple times for the same stream」と定め、2 回目を受信した側に WT_STREAM_STATE_ERROR を送る MUST を課している。`src/bindings/webtransport_h2.cpp` の `H2Session::stop_sending` は、入力検証・セッション終了の確認・ストリームの存在確認は行うが、既に送出済みかどうかを見ない。そのためアプリが同じストリームへ 2 回呼ぶと、そのたびに WT_STOP_SENDING を送出して MUST NOT に違反する。

違反した側は気付けないが、コンプライアントなピアは `H2Session::handle_wt_stop_sending` の二重受信検出で WT_STREAM_STATE_ERROR を返してセッションを終了させる。利用者から見ると「stop_sending の二重呼び出し (リトライや後始末の二重実行) でセッションが落ちる」という実害になる。

## 現状

- `H2Session::stop_sending` の早期離脱は 3 つある。`stream_id` が 2^62 以上なら `std::invalid_argument`、セッションが無いか終了済みなら return、`WtSessionInfo::streams` に該当エントリが無ければ return。この 3 つを通過すると、送出済みかどうかによらず毎回 `send_capsule` で WT_STOP_SENDING を積む
- 送出済みの記録は `WtSessionInfo::sent_stop_sending_stream_ids` に既にある。ただし参照しているのは `H2Session::maybe_send_max_stream_data` の抑止判定だけで、`H2Session::stop_sending` 自身は参照していない
- 受信側の 2 回目検出は `H2Session::handle_wt_stop_sending` に実装済みである。`WtSessionInfo::received_stop_sending_stream_ids` と `WtStreamInfo::stop_sending_received` で判定し、`H2Session::report_stream_state_error` を呼ぶ
- 0210 は本挙動をスコープ外と明記して完了しており、その受け皿として本 issue を起票した
- `tests/` に、同じストリームへ `H2Session::stop_sending` を 2 回呼んでワイヤを検証するテストが無い

## 設計方針

- `H2Session::stop_sending` の既存の早期 return の後、カプセル送出の前に `WtSessionInfo::sent_stop_sending_stream_ids` を確認し、既に含まれるなら黙って無視する
- WT_STOP_SENDING は「このストリームの受信を破棄する」という冪等な要求であり、2 回目は同じ意図の再送にすぎない。`H2Session::reset_stream` / `H2Session::send_stream_data` / `H2Session::send_datagram` が実在しない対象への操作を黙って無視しているのと同じ扱いにし、例外にはしない
- 記録の挿入位置は現状のまま (存在確認の後・送出の前) とし、無視する経路では記録を増やさない。挿入より前に判定を置く理由をコメントに残す
- 1 ストリームにつき 1 回だけ送出されることを利用者向けドキュメントに明記する。0210 が同じ docstring と SKILL.md の段落、および `WtSessionInfo::sent_stop_sending_stream_ids` の宣言コメントを編集しているため、続けて書く
- 実装順序: 0236 が同じ `WtSessionInfo::sent_stop_sending_stream_ids` の置き場所 (セッション単位のままか `WtStreamInfo` へ戻すか) を見直す。0236 を先に実装する場合はその結論に合わせて判定式と宣言コメントを書き、本 issue を先に実装する場合は 0236 側で本 issue の追記を踏まえて整合を取る。どちらの順でも、判定条件は「そのストリームへ WT_STOP_SENDING を送出済みか」だけとし、記録の置き場所に依存しない形にする

## 完了条件

- 同じストリームへ `H2Session::stop_sending` を複数回呼んでも、ワイヤに現れる WT_STOP_SENDING カプセルが 1 個である
- 2 回目以降の呼び出しが、ローカル側のイベント発生・ピア側の WT_STREAM_STATE_ERROR (0x51)・セッション終了のいずれも引き起こさない。`stream_id` が 2^62 以上の場合の `ValueError` は従来どおり送出される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `H2Session::stop_sending` に送出済み判定を追加し、`WtSessionInfo::sent_stop_sending_stream_ids` に含まれる場合は `send_capsule` を呼ばずに return する。判定を挿入位置より前に置く理由をコメントに残す
- `src/bindings/webtransport_h2.h` の `WtSessionInfo::sent_stop_sending_stream_ids` の宣言コメントに、抑止だけでなく送出済み判定にも使うことを追記する (0210 は抑止の用途しか書いていない)
- `src/webtransport/h2/client.py` と `src/webtransport/h2/server.py` の `stop_sending` の docstring、および `skills/webtransport-py/SKILL.md` に、同じストリームへ複数回呼んでも WT_STOP_SENDING は 1 回だけ送出される旨を追記する
- `tests/conftest.py` の `_create_small_stream_limit_h2_session_pair` を使い、同じストリームへ `stop_sending` を 2 回呼んでもワイヤ上の WT_STOP_SENDING (Type 0x190B4D3A) が 1 個であることを部分列チェックで検証する。0210 の抑止テスト (`_assert_no_max_stream_data_sent`) と同じくカプセル種別で判定し、値に依存しない表明にする。テストは `tests/test_webtransport_h2_flow_control_replenishment.py` の `stop_sending` を扱うテストの並びに置く
- あわせて、2 回目の呼び出し後も WT_MAX_STREAM_DATA の抑止 (Section 6.6 の MUST NOT) が維持されることを確認する
