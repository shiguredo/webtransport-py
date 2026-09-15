# WebTransport over HTTP/2 で WT_STOP_SENDING 後の WT_MAX_STREAM_DATA を検出・抑止する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-wt-max-stream-data-after-stop-sending
- Polished: 2026-09-15

## 目的

`refs/webtrans/draft-ietf-webtrans-http2-15.txt` の Section 6.6 は次の 2 つを要求している。

- A WT_MAX_STREAM_DATA capsule MUST NOT be sent after a sender requests that a stream be closed with WT_STOP_SENDING.
- A stream error (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent if a WT_MAX_STREAM_DATA capsule is received after a WT_STOP_SENDING capsule for the same stream.

現状はどちらも実装されておらず、仕様の MUST / MUST NOT を満たしていない。検出漏れは「不正なカプセルを黙って受理する実装」として相互運用時の不整合になる。

## 現状

受信側:

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` は `is_receivable_flow_capsule` による方向検証と減少値検査のみを行い、WT_STOP_SENDING を受信済みかどうかを見ていない
- 停止状態は `src/bindings/webtransport_h2.h` の `WtStreamInfo::stop_sending_received` と `WtSessionInfo::received_stop_sending_stream_ids` に保持されているが、設定するのは `H2Session::handle_wt_stop_sending` のみで、参照も同関数の二重受信検出に限られている
- 2 つの記録は役割が分かれている。セッション単位の集合はストリームエントリが無い場合 (未作成、または `H2Session::maybe_release_stream` による解放後) を担い、ストリーム単位のフラグは集合が安全弁の上限 (`kMaxReceivedMapEntries`) に達して挿入されなかった実在ストリームを担う
- 同種の違反検出は `H2Session::report_stream_state_error` に集約されており、呼び出しを 1 つ足せば済む

送信側:

- `H2Session::stop_sending` は WT_STOP_SENDING カプセルを送出するだけで、送出済みを記録しない
- 記録が無いため `H2Session::maybe_send_max_stream_data` を抑止できず、WT_STOP_SENDING 送出後に同じストリームへ WT_MAX_STREAM_DATA を送出し得る
- 記録を `WtStreamInfo` に持つと、両ハーフ終端で `H2Session::maybe_release_stream` がエントリを解放した時点で失われる。解放後にピアが同じ Stream ID へ WT_STREAM を送ると `H2Session::handle_wt_stream` が暗黙作成するため、記録無しで `H2Session::maybe_send_max_stream_data` に到達し得る

テスト:

- この条件を検証するテストが存在しない

## 設計方針

- 受信側は `H2Session::handle_wt_max_stream_data` の方向検証の後で当該ストリームの停止状態を確認し、該当すれば `H2Session::report_stream_state_error` を呼ぶ。判定は `H2Session::handle_wt_stop_sending` の二重受信検出と同じ条件 (`WtSessionInfo::received_stop_sending_stream_ids` に含まれる、または実在ストリームの `WtStreamInfo::stop_sending_received` が真) を使い、未作成ストリームと解放後のストリームも検出する。判定は減少値検査より前に置き、仕様が要求するエラー種別 (WT_STREAM_STATE_ERROR) を優先する
- 送信側は `WtSessionInfo` に送出済み Stream ID の集合を追加し、`H2Session::stop_sending` の送出時に挿入、`H2Session::maybe_send_max_stream_data` の先頭で照合して抑止する。受信側の集合と同じくエントリ解放後も記録を残す (ストリーム単位のフラグでは解放後の送出を抑止できないため)。`H2Session::stop_sending` は実在ストリームにしか送出しないため、この集合は実在ストリームの ID だけを含み、要素数は自側が停止したストリーム数に従う。ピアが任意に選べる ID を受信側の集合と違って追加できないため、受信側の集合にあるメモリ DoS 防止の安全弁は設けず、抑止を無条件に保つ。ストリームが未登録で `stop_sending` が黙って無視する経路では挿入しない
- エラー通知の実体は既存の `H2Session::report_stream_state_error` をそのまま使う。ストリームエラー (RST_STREAM) で送るべきかは Section 3.4 の解釈が割れるため、本 issue では検出の追加のみを行い、通知機構の見直しは扱わない
- 解放後の WT_STREAM が暗黙作成されて Section 6.4 の終端検出を免れる既存挙動は本 issue のスコープ外とする (本 issue は抑止側の記録を解放後も維持することで MUST NOT を満たす)

## 完了条件

- WT_STOP_SENDING を受信したストリームへ WT_MAX_STREAM_DATA が届いたとき、WT_STREAM_STATE_ERROR が通知される
  - 未作成ストリーム、またはエントリ解放後の Stream ID でも検出する
- WT_STOP_SENDING を送出した後は、同じストリームへ WT_MAX_STREAM_DATA が送出されない
  - エントリ解放後にピアが同じ Stream ID へ WT_STREAM を送って暗黙作成された場合も送出しない
- 上記 2 つを検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` に停止状態の確認と `H2Session::report_stream_state_error` の呼び出しを追加する。判定条件は `H2Session::handle_wt_stop_sending` の二重受信検出と同じにする
- `src/bindings/webtransport_h2.h` の `WtSessionInfo` に WT_STOP_SENDING 送出済み Stream ID の集合を追加し、`H2Session::stop_sending` で挿入、`H2Session::maybe_send_max_stream_data` で照合して抑止する
- `tests/test_webtransport_h2_stream_state_error.py` に受信側の検出テスト (未作成ストリームと解放後のストリームを含む) を追加し、`tests/test_webtransport_h2_flow_control_replenishment.py` に送信側の抑止テストを追加する
