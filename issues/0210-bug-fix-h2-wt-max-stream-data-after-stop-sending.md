# WebTransport over HTTP/2 で WT_STOP_SENDING 後の WT_MAX_STREAM_DATA を検出・抑止する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-wt-max-stream-data-after-stop-sending
- Polished: {YYYY-MM-DD}

## 目的

`refs/webtrans/draft-ietf-webtrans-http2-15.txt` の Section 6.6 は次の 2 つを要求している。

- A WT_MAX_STREAM_DATA capsule MUST NOT be sent after a sender requests that a stream be closed with WT_STOP_SENDING.
- A stream error (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent if a WT_MAX_STREAM_DATA capsule is received after a WT_STOP_SENDING capsule for the same stream.

現状はどちらも実装されておらず、仕様の MUST / MUST NOT を満たしていない。検出漏れは「不正なカプセルを黙って受理する実装」として相互運用時の不整合になる。

## 現状

受信側:

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` は `is_receivable_flow_capsule` による方向検証と減少値検査のみを行い、WT_STOP_SENDING を受信済みかどうかを見ていない
- 停止状態は `src/bindings/webtransport_h2.h` の `WtStreamInfo::stop_sending_received` と `H2Session::received_stop_sending_stream_ids` に保持されているが、設定するのは `H2Session::handle_wt_stop_sending` のみで、参照も同関数の二重受信検出に限られている
- 同種の違反検出は `H2Session::report_stream_state_error` に集約されており、呼び出しを 1 つ足せば済む

送信側:

- `H2Session::stop_sending` は WT_STOP_SENDING カプセルを送出するだけで、送出済みを記録しない
- 記録が無いため `H2Session::maybe_send_max_stream_data` を抑止できず、WT_STOP_SENDING 送出後に同じストリームへ WT_MAX_STREAM_DATA を送出し得る

テスト:

- この条件を検証するテストが存在しない

## 設計方針

- 受信側は `H2Session::handle_wt_max_stream_data` の方向検証の後で当該ストリームの停止状態を確認し、該当すれば `H2Session::report_stream_state_error` を呼ぶ。判定は減少値検査より前に置き、仕様が要求するエラー種別 (WT_STREAM_STATE_ERROR) を優先する
- 送信側は `H2Session::stop_sending` の送出時に `WtStreamInfo` へ送出済みフラグを立て、`H2Session::maybe_send_max_stream_data` の先頭で抑止する。ストリームが未登録で `stop_sending` が黙って無視する経路ではフラグを立てない
- エラー通知の実体は既存の `H2Session::report_stream_state_error` をそのまま使う。ストリームエラー (RST_STREAM) で送るべきかは Section 3.4 の解釈が割れるため、本 issue では検出の追加のみを行い、通知機構の見直しは扱わない

## 完了条件

- WT_STOP_SENDING を受信したストリームへ WT_MAX_STREAM_DATA が届いたとき、WT_STREAM_STATE_ERROR が通知される
- WT_STOP_SENDING を送出した後は、同じストリームへ WT_MAX_STREAM_DATA が送出されない
- 上記 2 つを検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` に停止状態の確認と `H2Session::report_stream_state_error` の呼び出しを追加する
- `src/bindings/webtransport_h2.h` の `WtStreamInfo` に WT_STOP_SENDING 送出済みフラグを追加し、`H2Session::stop_sending` で設定、`H2Session::maybe_send_max_stream_data` で参照する
- `tests/test_webtransport_h2_stream_state_error.py` に受信側の検出テストを追加し、フロー制御カプセル系のテストに送信側の抑止テストを追加する
