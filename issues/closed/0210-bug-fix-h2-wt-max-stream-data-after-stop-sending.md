# WebTransport over HTTP/2 で WT_STOP_SENDING 後の WT_MAX_STREAM_DATA を検出・抑止する

- Created: 2026-09-15
- Completed: 2026-09-18
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
- 記録を `WtStreamInfo` に持つと、`H2Session::maybe_release_stream` がエントリを解放した時点で失われる (双方向は両ハーフ終端、単方向は使う方向の終端で解放される)。解放後にピアが同じ Stream ID へ WT_STREAM を送ると `H2Session::handle_wt_stream` が暗黙作成するため、記録無しで `H2Session::maybe_send_max_stream_data` に到達し得る

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

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` に停止状態の確認を追加し、WT_STOP_SENDING を受信済みのストリームへ WT_MAX_STREAM_DATA が届いた場合は `H2Session::report_stream_state_error` で WT_STREAM_STATE_ERROR を通知するようにした。判定は方向検証の後・減少値検査の前に置いた (draft は 2 つの MUST が同時に成立する場合の優先を定めていないため、本判定を先に置くのは実装の選択である)
- 停止状態の判定は匿名 namespace の `has_received_stop_sending` に集約した。セッション単位の集合 `WtSessionInfo::received_stop_sending_stream_ids` が未作成・エントリ解放後のストリームを担い、`WtStreamInfo::stop_sending_received` が集合の安全弁上限に達した場合の実在ストリームを担う (従来は `H2Session::handle_wt_stop_sending` の二重受信検出だけが同じ条件を使っていた)
- `src/bindings/webtransport_h2.h` の `WtSessionInfo` に `sent_stop_sending_stream_ids` を追加し、`H2Session::stop_sending` が送出済みを記録、`H2Session::maybe_send_max_stream_data` の先頭で照合して抑止するようにした。記録はストリームエントリの解放後も保持する (解放後にピアが同じ Stream ID へ WT_STREAM を送ると `H2Session::handle_wt_stream` が暗黙作成するため、エントリ単位の記録では抑止できない)。有界化するとその分だけ MUST NOT を満たせなくなるため上限は設けず、要素数はセッション生存中に停止を要求した実在ストリーム数に比例して増える (受信側の集合がメモリ DoS 対策で有界なのとは前提が異なる)
- `tests/test_webtransport_h2_stream_state_error.py` に 5 件追加した。停止済みストリームへの WT_MAX_STREAM_DATA の検出 (減少値と同時に成立する場合は WT_STREAM_STATE_ERROR を優先)、未作成ストリームでの検出、エントリ解放後の検出、停止状態でないストリームでの受理 (別ストリームと停止なしの 2 件)
- `tests/test_webtransport_h2_flow_control_replenishment.py` に 4 件追加した。停止後の抑止 (対照として停止なしでは 13 の WT_MAX_STREAM_DATA が送出されることも固定)、エントリ解放後に暗黙作成されたストリームでの抑止、自側が WT_STOP_SENDING を送出したストリームの WT_MAX_STREAM_DATA は受理されクレジットが反映されることの回帰ピン。抑止の否定表明はカプセル種別 (0x190B4D3E) で判定し、補充量の式が変わっても空振りしないようにした
- `src/webtransport/h2/client.py` と `src/webtransport/h2/server.py` の `stop_sending` の docstring に送出後の WT_MAX_STREAM_DATA 抑止 (Section 6.6 の MUST NOT) を、`skills/webtransport-py/SKILL.md` に同じ抑止と停止要求後もピアが WT_RESET_STREAM を返すまでデータが届き続けることを追記した
- `tests/conftest.py` に `_encode_wt_max_stream_data_capsule` を追加し、`tests/test_webtransport_h2_received_map_bound.py` にあった同一実装の重複を解消した
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 本対応のスコープ外: 同じストリームへ `H2Session::stop_sending` を 2 回呼ぶと WT_STOP_SENDING が 2 回送出される既存挙動 (Section 6.3 の MUST NOT) は変更していない
