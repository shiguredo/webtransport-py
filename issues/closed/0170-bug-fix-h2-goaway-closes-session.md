# WebTransport over HTTP/2 が GOAWAY 受信で is_closed 化し既存セッションを継続できない

- Created: 2026-09-07
- Completed: 2026-09-09
- Branch: feature/fix-h2-goaway-closes-session
- Polished: 2026-09-07

## 目的

`H2Session::on_frame_recv_callback` は `NGHTTP2_GOAWAY` を受信すると `closed_ = true` を立てる。高レベル `h2.Client` は `is_closed()` を検知して `run()` の継続を止め、`h2.Server` は `_handle_client` のループを抜けて後始末に入る。draft-ietf-webtrans-http2-15 Section 6.13「After sending or receiving either a WT_DRAIN_SESSION capsule or HTTP/2 GOAWAY frame, an endpoint MAY continue using the session and MAY open new WebTransport streams」に反し、graceful shutdown を送ってきた正当なピアに対して既存セッションを打ち切る。`Http2Connection` 側は同じ GOAWAY 受信を graceful (`goaway_received_` フラグで新規ストリームのみ抑止) として扱っており、2 層で解釈が正反対。issue 0155 の h3 版と同型のバグを h2 側にも抱える。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の `NGHTTP2_GOAWAY` 分岐は `h2_session->closed_ = true;` のみ
- `src/webtransport/h2/client.py` の `Client.run` と `src/webtransport/h2/server.py` の `Server._handle_client` は `session.is_closed()` を検知してループを抜ける
- 対照: `src/bindings/http2.cpp` の `Http2Connection::on_frame_recv_callback` は `GoAway` イベントを積み `goaway_received_ = true` を立てるが `closed_` にはしない。RFC 9113 Section 6.8 の graceful shutdown を尊重するコメント付き
- draft-15 Section 6.13 (GOAWAY 後もセッションを使い続けてよい)、Section 3.4 (セッション終了条件は CONNECT ストリームのクローズ。原文「A WebTransport session is terminated when the CONNECT stream that created it is closed」)
- `H2EventType::SessionDraining` は `WT_DRAIN_SESSION` カプセル受信用に定義済みであり、GOAWAY 用には流用しない (0155 の確定方針に従う)
- WT h2 系テスト (`tests/test_webtransport_h2*.py` / `tests/test_e2e_webtransport_h2.py`) に GOAWAY 受信を扱うテストは存在しない (当該パスでの `goaway` の出現 0 件)

## 設計方針

- `H2Session::on_frame_recv_callback` の `NGHTTP2_GOAWAY` 分岐で `closed_` を立てるのを止める。代わりにコネクション単位の新規イベント `H2EventType::GoAway` (GOAWAY の last_stream_id と error_code 付き) を積む。`SessionDraining` 流用は選ばない (WT_DRAIN_SESSION 用であり GOAWAY と仕様上の区別があるため。0155 の確定方針に従う)
- 新規イベントは `H2EventType` の末尾に追加し、`h2.pyi` の `EventType` にも公開する (0133 / 0140 の確立規約に従い既存値の数値を変えない)。enum 登録も追加する
- 新規 CONNECT 要求のみを抑止するフラグ (`goaway_received_` を新設。`Http2Connection` と対称) を立てる。既存セッションの送受信と既存セッション上の WT ストリーム open は継続する (Section 6.13 の MAY 継続に従う)
- 高レベル `h2.Client` / `h2.Server` は `GoAway` を `on_goaway` コールバックとして通知する。シグネチャは Client が `(last_stream_id, error_code)`、Server が `(last_stream_id, error_code, addr)` とし、発火は初回受信のみとする。既存セッションの送受信は継続する
- 死にフィールド `goaway_sent_` は本 issue の対象外とし、open issue 0124 に委ねる

## 対象・対象外

- 対象: `src/bindings/webtransport_h2.cpp` (`NGHTTP2_GOAWAY` 分岐と enum 登録) / `src/bindings/webtransport_h2.h` (`H2EventType::GoAway`) / `src/webtransport/h2.pyi` (`GOAWAY` 公開) / `src/webtransport/h2/client.py` と `server.py` (`on_goaway` と `is_closed()` 分岐除外) / `tests/test_webtransport_h2_goaway.py` (新規、低レベル) と `tests/test_e2e_webtransport_h2.py` (高レベル) / `CHANGES.md`
- 対象外: `goaway_sent_` の処分 (0124 の所有) / `drain_session` 送受信 (0122 の所有) / `http2.cpp`

## 依存関係

- open issue 0155 (h3 版) と単位・命名を GoAway 系で揃える。本 issue は h2 版として 0155 方式を踏襲する

## 完了条件

- GOAWAY 受信後に `is_closed()` が偽のままであり、既存 WT セッションで送受信が継続できること (低レベル Sans-IO で確認する)
- 新規 CONNECT 要求は抑止されること
- 初回 GOAWAY 受信で `on_goaway` が 1 回発火すること
- `tests/test_webtransport_h2_goaway.py` (新規) に GOAWAY フレーム注入による継続テスト (低レベル) を追加し、`tests/test_e2e_webtransport_h2.py` に継続と `on_goaway` 発火のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- GOAWAY 受信で閉じず GoAway 通知と継続印に変える。新規 CONNECT のみ抑止し、既存の送受信と WT 開放は続ける
- 高レベル両面に初回のみの通知を足し、低レベルと高レベルに 7 件の試験を足す
- 全 976 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
