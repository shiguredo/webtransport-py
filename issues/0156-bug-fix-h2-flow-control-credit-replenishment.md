# WebTransport over HTTP/2 のフロー制御クレジット (WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS) を初期値の 1 回しか送らずセッション寿命の転送量が固定される

- Created: 2026-09-06
- Completed: 2026-09-08
- Branch: feature/fix-h2-flow-control-credit-replenishment
- Polished: 2026-09-07

## 目的

WebTransport over HTTP/2 のフロー制御クレジット送出が初期値の 1 回のみで、受信消費に応じた補充がない。セッション寿命で受信できる総量が `wt_initial_max_data` (既定 1 MiB)、ストリームあたり `wt_initial_max_stream_data` (既定 256 KiB)、ストリーム数は累積 100 本に固定され、超えるとピアを WT_FLOW_CONTROL_ERROR (プレースホルダ 0x50) で自ら切る。draft-ietf-webtrans-http2-15 Section 4.4 の SHOULD「Endpoints SHOULD send flow control credit as they consume data or close streams」に反する。加えて Section 6.7 の Note (Maximum Streams は累積値であり、ストリーム開始に伴い増加値を送り直す) に沿った再送出もない。送信側は超過送信を試みた瞬間に理由通知なしでセッションを自己クローズしてしまう (仕様違反ではないが、ブロック・保留方式に変える)。本 issue の作業範囲はクレジット補充・送信側の保留化・両ハーフ終端エントリ解放であり、残クレジット観測 API は別途 issue 化する。

## 現状

- `WtMaxData` / `WtMaxStreamsBidi` / `WtMaxStreamsUni` を `send_capsule` する箇所は `src/bindings/webtransport_h2.cpp` の `H2Session::accept_session` と、2xx 応答受信時の 2 箇所のみ。`WtMaxStreamData` の送出箇所は 0 件 (grep 済み)
- `handle_wt_stream` で `bytes_received` を加算するが `max_data_remote` / `max_stream_data_remote` / `max_streams_*_remote` を増やして新しい `WT_MAX_DATA` / `WT_MAX_STREAM_DATA` / `WT_MAX_STREAMS` を送る経路が無い
- `WT_DATA_BLOCKED` / `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` の送出も 0 件 (送出自体は Section 6.8 / 6.9 / 6.10 で任意とされるが、送信側がブロック方式を取らないため送る場面自体が無い)
- `send_stream_data` の送信側で `wt_session->bytes_sent + data.size() > wt_session->max_data_local` を検知すると `report_flow_control_error` → `close_session` で自己クローズ (`Error` イベントを push せずアプリからは理由不明でセッションが閉じたようにしか見えない)
- 実験手順 (Python API 形式): H2Session ペアを既定 Config (`wt_initial_max_data` 1048576 / `wt_initial_max_stream_data` 262144 / bidi・uni 各 100) で確立し、(a) 4 本の双方向ストリームに各 256 KiB 送信後に 5 本目へ 1 byte 送信、(b) 1 本のストリームに 256 KiB + 1 byte 送信、(c) 双方向 100 本を開いて FIN で閉じた後に 101 本目の `open_stream` を呼ぶ。いずれもセッション閉または -1 になる方向性はコードと整合するが、実行記録は未取得のため数値の再測定は実装時に行う
- 受信側 (`handle_wt_max_data` / `handle_wt_max_streams` / `handle_wt_max_stream_data`) は減少値と `WT_MAX_STREAMS` 系の 2^60 超を WT_FLOW_CONTROL_ERROR で閉じる MUST の実装は入っている

## 設計方針

- 受信消費とストリーム終了に応じたクレジット送出を追加する。`WT_MAX_DATA` は `bytes_received > max_data_remote / 2` のヒステリシス到達で送出し、`WT_MAX_STREAM_DATA` はストリーム単位の受信量で同型の 1/2 ヒステリシスとする。`WT_MAX_STREAMS` はストリーム終了時に現広告値 + 終了本数を送出する (累積値のため)
- 送信側は超過送信を「エラーではなくキュー保留」に変える。超過試行の初回のみ `WT_DATA_BLOCKED` / `WT_STREAM_DATA_BLOCKED` / `WT_STREAMS_BLOCKED` を送出する (同一制限値での重複送出はしない。Section 6.8 / 6.9 / 6.10 の SHOULD に従う)。保留データは既存のストリーム送信バッファに保持し、新規の無制限バッファは作らない (0158 型の蓄積を作らないため)。`open_stream` の上限超過時は従来どおり -1 を返し、`WT_STREAMS_BLOCKED` 送出を伴う
- 両ハーフ終端 (`send_state` が `DataSent` / `ResetSent` かつ `recv_state` が `DataRecvd` / `ResetRecvd`) のストリームエントリ (`WtStreamInfo`) をセッションから解放する (メモリリーク回避)。`reset_stream` の「erase しない」設計コメントも合わせて更新する。本 issue が実装責任を持ち、0158 側の重複記載は 0158 の deep 分離時に除去する
- 残クレジット観測 API (`get_send_credit` 等) は本 issue に含めず別途 issue 化する (ワイヤ観測で回帰検証できるため必須ではない)
- 既存の受信側 MUST 実装 (減少値・2^60 超) には手を入れない (正しい)
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` (`WtSessionInfo` / `WtStreamInfo` / 送出経路) のみとし、Python 高レベル層の変更は行わない

## 完了条件

- 1 MiB を超えるセッション転送、256 KiB を超える単一ストリーム転送、100 本を超えるストリーム開設が自己クローズせずに完了すること
- 送信側は超過試行の初回に `WT_*_BLOCKED` を送出し、ピアからの `WT_MAX_*` を受信すると送出が再開されること (ワイヤのカプセル列で確認する)
- 両ハーフ終端したストリームエントリがセッションから解放されること
- `tests/` に 1 MiB 超・256 KiB 超・101 本の 3 経路に加え、BLOCKED 送出・再開とエントリ解放のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- 受信消費の 1/2 到達で `WT_MAX_DATA` と `WT_MAX_STREAM_DATA` を初期値分上乗せして送出し、`WT_MAX_STREAMS` はストリーム終了時に現広告値 + 1 で補充する (累積値のため)
- 送信超過は自己クローズせず、残量分を部分送出して残りを保留キューへ積む。超過試行の初回のみ `BLOCKED` 系を送出し、対向の `MAX` 受信で自動送出を再開する。`open_stream` の上限超過時は従来どおり -1 を返し `WT_STREAMS_BLOCKED` を伴う
- 両ハーフ終端 (単方向は使用側ハーフ) のストリームエントリを解放する。リセット時は保留を破棄し、FIN 後の送信は無視する。未知 ID は initiator 導出で `MAX_STREAMS` 水増しを防ぐ
- `tests/test_webtransport_h2_flow_control_replenishment.py` に 14 件のテスト (1 MiB 超・256 KiB 超・101 本・BLOCKED 送出と再開・エントリ解放・部分 FIN 等) を追加し、旧自己クローズ期待の 4 件を新仕様に更新する
- 全 906 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
