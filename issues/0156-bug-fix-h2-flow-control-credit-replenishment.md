# WebTransport over HTTP/2 のフロー制御クレジット (WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS) を初期値の 1 回しか送らずセッション寿命の転送量が固定される

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-flow-control-credit-replenishment
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 のフロー制御クレジット送出が初期値の 1 回のみで、受信消費に応じた補充がない。セッション寿命で受信できる総量が `wt_initial_max_data` (既定 1 MiB)、ストリームあたり `wt_initial_max_stream_data` (既定 256 KiB)、ストリーム数は累積 100 本に固定され、超えるとピアを WT_FLOW_CONTROL_ERROR (プレースホルダ 0x50) で自ら切る。draft-ietf-webtrans-http2-15 Section 6.7 の「endpoints repeatedly send new WT_MAX_STREAMS capsules with increasing Maximum Streams values as streams are opened」の SHOULD、および Section 4.4 / 6.8 / 6.9 / 6.10 の SHOULD を守っていない。加えて Section 6.5 / 6.6 の MUST close は受信側の義務で、送信側は MUST NOT exceed のみだが本実装は送信側でアプリが超過送信を試みた瞬間にセッションを自己クローズしてしまう (誤り)。

## 現状

- `WtMaxData` / `WtMaxStreamsBidi` / `WtMaxStreamsUni` を `send_capsule` する箇所は `src/bindings/webtransport_h2.cpp` の `H2Session::accept_session` と、2xx 応答受信時の 2 箇所のみ。`WtMaxStreamData` の送出箇所は 0 件 (grep 済み)
- `handle_wt_stream` で `bytes_received` を加算するが `max_data_remote` / `max_stream_data_remote` / `max_streams_*_remote` を増やして新しい `WT_MAX_DATA` / `WT_MAX_STREAM_DATA` / `WT_MAX_STREAMS` を送る経路が無い
- `WT_DATA_BLOCKED` / `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` の送出も 0 件
- `send_stream_data` の送信側で `wt_session->bytes_sent + data.size() > wt_session->max_data_local` を検知すると `report_flow_control_error` → `close_session` で自己クローズ (`Error` イベントを push せずアプリからは理由不明でセッションが閉じたようにしか見えない)
- 実験: (a) 4 ストリーム × 256 KiB = 1 MiB の後 5 本目に 1 byte → セッション閉、(b) 1 ストリームで 256 KiB + 1 byte → セッション閉、(c) 100 本開いて FIN で閉じた後 101 本目の `open_stream` は -1
- 受信側 (`handle_wt_max_data` / `handle_wt_max_streams` / `handle_wt_max_stream_data`) は減少値と 2^60 超を WT_FLOW_CONTROL_ERROR で閉じる MUST の実装は入っている

## 設計方針

- 受信消費とストリーム終了に応じた閾値方式のクレジット送出を追加する: `bytes_received > max_data_remote / 2` のようなヒステリシスで `WT_MAX_DATA` を、ストリーム終了時に `WT_MAX_STREAMS` を送出する
- ストリーム単位も同型で `WT_MAX_STREAM_DATA` を追加する
- 送信側は超過送信を「エラーではなくキュー保留」に変え、`WT_DATA_BLOCKED` / `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` を送出する (Section 6.8 / 6.9 / 6.10 の SHOULD)
- アプリが残クレジットを観測できる公開 API を追加する (`H2Session::get_send_credit(session_id)` 等)
- 両ハーフ終端後のストリームエントリ (`WtStreamInfo`) をセッションから解放する (メモリリーク回避)
- 既存の受信側 MUST 実装 (減少値・2^60 超) には手を入れない (正しい)

## 完了条件

- 1 MiB を超えるセッション転送、256 KiB を超える単一ストリーム転送、100 本を超えるストリーム開設が自己クローズせずに完了すること
- 送信側は超過時に `WT_*_BLOCKED` を送出し、ピアからの `WT_MAX_*` を受信すると送出が再開されること
- 両ハーフ終端したストリームエントリがセッションから解放されること
- `tests/` に 1 MiB 超・256 KiB 超・101 本の 3 経路の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
