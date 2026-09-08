# WebTransport over HTTP/2 のストリーム ID の initiator ビットと方向を検証せず ID 衝突で状態が壊れる

- Created: 2026-09-06
- Completed: 2026-09-09
- Branch: feature/fix-h2-stream-id-initiator-validation
- Polished: 2026-09-07

## 目的

WebTransport over HTTP/2 の実装は WT ストリーム ID の initiator ビット (bit 0) と方向性 (bit 1) を検証しない。ピアが本実装側の initiator を持つ stream_id で WT_STREAM を送ってきても新規エントリとして受け入れ、`open_stream` が同じ ID を後で払い出すと既存エントリが上書きされる。draft-ietf-webtrans-http2-15 Section 5.2 のストリーム状態は RFC 9000 の識別子 (Section 2.1) と状態 (Section 3) のミラーであり、initiator / 方向違反は仕様違反である。検証はカプセル種別の向きごとに行う。送信者→受信者方向 (WT_STREAM / WT_RESET_STREAM) は自側送信専用 (自側 initiator + uni) への受信を、受信者→送信者方向 (WT_STOP_SENDING / WT_MAX_STREAM_DATA) は自側受信専用 (ピア initiator + uni) への受信を、それぞれ WT_STREAM_STATE_ERROR で拒否する。ピアの受信専用ストリームに送信できてしまう経路も同根で塞ぐ。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stream` は未知 stream_id を常に `is_local = false` で新規作成 (initiator 未検証)
- `H2Session::open_stream` は既存エントリを上書き (`wt_session->streams[stream_id] = info`)
- `H2Session::send_stream_data` は方向を検査せず、受信専用 (自側から見てピア initiator + uni) への送信を塞がない
- `H2Session::handle_wt_stop_sending` / `handle_wt_reset_stream` / `handle_wt_max_stream_data` は方向を検査せず、自側受信専用への `WT_STOP_SENDING` / `WT_MAX_STREAM_DATA` 受信と自側送信専用への `WT_RESET_STREAM` 受信を受理してしまう
- 実験手順 (Sans-IO の H2Session ペア。endpoint を明示する):
  - (a) クライアントが ID 1 (サーバー起点双方向。自側=サーバーから見て自側 initiator の未作成ストリーム) で WT_STREAM を送るとサーバーは受理し、その後サーバーの `open_stream` が同じ ID 1 を払い出して上書きする (衝突)
  - (b) クライアントが自側送信専用 ID 2 (クライアント起点単方向) へのサーバーからの DATA を `StreamData` として配送する (拒否すべき)
  - (c) サーバーが自側受信専用 ID 2 (クライアント起点単方向) に `send_stream_data` すると WT_STREAM がワイヤに出る (黙殺すべき)
  - (d) クライアントが自側受信専用 ID 3 (サーバー起点単方向) への WT_STOP_SENDING を `StopSending` イベントとして受理する (拒否すべき)。逆に自側送信専用への WT_STOP_SENDING 受信は正規動作であり受理を維持する
- `incoming_stream_exceeds_limit` の `(stream_id >> 2) + 1` 計算そのものは 4 種別共通の正しい計算 (initiator ビットとは独立)

## 設計方針

- 受信検証はカプセル種別の向きごとに行う。送信者→受信者方向 (`handle_wt_stream` / `handle_wt_reset_stream`) は自側送信専用 (自側 initiator + uni) への受信を、受信者→送信者方向 (`handle_wt_stop_sending` / `handle_wt_max_stream_data`) は自側受信専用 (ピア initiator + uni) への受信を、それぞれ WT_STREAM_STATE_ERROR で拒否する。拒否方式は既存 `report_stream_state_error` (Error イベント push 後に `close_session`) を使う (0084 の前例に倣う)。`handle_wt_streams_blocked` は stream ID を持たないため対象外とする
- `send_stream_data` は受信専用 (自側から見てピア initiator + uni) への送信を黙って無視する (現行の黙殺流儀を維持し、セッションは閉じない)
- `open_stream` は既存エントリの上書きを禁止する。存在確認を ID 払い出しの前に行い、エントリが既にあればカウンタを消費せず -1 を返す
- `stream_id % 4` の値と (`is_server_`, 送受信方向) の対応表を `webtransport_h2.cpp` の無名名前空間の static 関数 2 本 (`is_receivable_data_capsule` は送信者→受信者方向用、`is_receivable_flow_capsule` は受信者→送信者方向用。いずれも引数は stream_id と自側サーバー判定) に集約し、4 ハンドラから呼ぶ。真理値表は次とする。双方向 (%4==0/1) は両方向とも可。単方向は送信者→受信者方向がピア initiator のみ可 (クライアント受信では %4==3 のみ可、サーバー受信では %4==2 のみ可)、受信者→送信者方向が自側 initiator のみ可 (クライアント受信では %4==2 のみ可、サーバー受信では %4==3 のみ可)
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとする

## 完了条件

- 自側 initiator を持つ未作成 ID への WT_STREAM 受信が WT_STREAM_STATE_ERROR で拒否されること
- 受信専用ストリームへの `send_stream_data` が黙って無視され、セッションが閉じないこと
- 自側受信専用への WT_STOP_SENDING / WT_MAX_STREAM_DATA 受信が WT_STREAM_STATE_ERROR で拒否され、自側送信専用への同受信は受理されること
- 既存の受理ロジック (双方向の送受信、自側送信専用への WT_STOP_SENDING 受信) が引き続き動作すること
- `tests/test_webtransport_h2_initiator_validation.py` を新規作成し、上記 4 経路の回帰テスト (Sans-IO ワイヤ注入による正常系・異常系の表形式) を追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- QUIC 互換 ID の方向検証ヘルパー 2 本を追加し、送信者→受信者方向は自側送信専用への受信を、受信者→送信者方向は自側受信専用への受信を `WT_STREAM_STATE_ERROR` で拒否する
- `send_stream_data` は受信専用への送信を黙殺し、`open_stream` は既存エントリの上書きを禁止する (カウンタ不消費)
- `tests/test_webtransport_h2_initiator_validation.py` に 12 件のテスト (4 経路の正常系と異常系・両視点) を追加する
- 全 935 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
