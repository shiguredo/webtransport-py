# WebTransport over HTTP/2 で同じストリームへの stop_sending が WT_STOP_SENDING を複数回送出する

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stop-sending-duplicate-send
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.3 は「A WT_STOP_SENDING capsule MUST NOT be sent multiple times for the same stream」と定め、2 回目を受信した側に WT_STREAM_STATE_ERROR を送る MUST を課している。`src/bindings/webtransport_h2.cpp` の `H2Session::stop_sending` は呼び出しごとに無条件でカプセルを送出するため、アプリが同じストリームへ 2 回呼ぶと MUST NOT に違反する。

違反した側は気付けないが、コンプライアントなピアは `H2Session::handle_wt_stop_sending` の二重受信検出で WT_STREAM_STATE_ERROR を返してセッションを終了させる。利用者から見ると「stop_sending の二重呼び出し (リトライや後始末の二重実行) でセッションが落ちる」という実害になる。

## 現状

- `H2Session::stop_sending` は `stream_id` の範囲検証・セッション終了の確認・ストリームの存在確認のみを行い、既に送出済みかどうかを見ない。存在確認を通過すれば毎回 `send_capsule` で WT_STOP_SENDING を積む
- 送出済みの記録は `WtSessionInfo::sent_stop_sending_stream_ids` に既にある。ただし参照しているのは `H2Session::maybe_send_max_stream_data` の抑止判定だけで、`H2Session::stop_sending` 自身は参照していない
- 受信側の 2 回目検出は `H2Session::handle_wt_stop_sending` に実装済みである。`WtSessionInfo::received_stop_sending_stream_ids` と `WtStreamInfo::stop_sending_received` で判定し、`H2Session::report_stream_state_error` を呼ぶ
- 0210 は本挙動をスコープ外と明記したが、対応する issue は起票されていない
- `tests/` に、同じストリームへ `H2Session::stop_sending` を 2 回呼んでワイヤを検証するテストが無い

## 設計方針

- `H2Session::stop_sending` の既存の早期 return (入力検証・セッション終了確認・ストリーム存在確認) の後、カプセル送出の前に `WtSessionInfo::sent_stop_sending_stream_ids` を確認し、既に含まれるなら黙って無視する
- 扱いは「存在しないストリーム ID への送出は黙って無視する (セッションは閉じない)」という既存方針と揃える。`stop_sending` は `void` を返す契約であり、2 回目を例外にすると公開 API の後方互換が崩れるため採用しない
- 記録の挿入位置は現状のまま (存在確認の後・送出の前) とし、無視する経路では記録を増やさない
- 1 ストリームにつき 1 回だけ送出されることを利用者向けドキュメントに明記する。0210 が同じ docstring と SKILL.md の段落を編集しているため、続けて書く

## 完了条件

- 同じストリームへ `H2Session::stop_sending` を複数回呼んでも、ワイヤに現れる WT_STOP_SENDING カプセルが 1 個である
- 2 回目以降の呼び出しが例外・セッション終了・イベントの重複発火を引き起こさない
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `H2Session::stop_sending` に送出済み判定を追加し、`WtSessionInfo::sent_stop_sending_stream_ids` に含まれる場合は `send_capsule` を呼ばずに return する。判定を挿入位置より前に置く理由をコメントに残す
- `src/webtransport/h2/client.py` と `src/webtransport/h2/server.py` の `stop_sending` の docstring、および `skills/webtransport-py/SKILL.md` に、同じストリームへ複数回呼んでも WT_STOP_SENDING は 1 回だけ送出される旨を追記する
- `tests/test_webtransport_h2_flow_control_replenishment.py` の小さい上限のセッションペアを使い、同じストリームへ `stop_sending` を 2 回呼んでもワイヤ上の WT_STOP_SENDING (Type 0x190B4D3A) が 1 個であることを部分列チェックで検証する。0210 の抑止テストと同じくカプセル種別で判定し、値に依存しない表明にする
- あわせて、2 回目の呼び出し後も WT_MAX_STREAM_DATA の抑止 (Section 6.6 の MUST NOT) が維持されることを確認する
