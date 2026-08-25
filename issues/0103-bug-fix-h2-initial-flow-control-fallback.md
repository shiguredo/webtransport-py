# WebTransport over HTTP/2 の初期フロー制御 0 フォールバックが広告制限を超える問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-h2-initial-flow-control-fallback
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http2-15 の MUST「WT_STREAM のデータ合計は受信者が広告した制限を超えてはならない」(Section 6.5) と「現在のストリーム上限を超えるストリームを開いてはならない」(Section 6.7) に反し、対向 SETTINGS が 0 (仕様デフォルト) の場合に自側 config 値を送信クレジット・ストリーム上限として使う問題を修正する。仕様では 0 は「WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS カプセル到着まで送信不可」(Section 11.2) を意味するため、現在のフォールバックは広告制限を超えた送信・開設となり、コンプライアントなピアから WT_FLOW_CONTROL_ERROR を受ける。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::apply_peer_initial_flow_control` は、対向 SETTINGS の値が 0 (または SETTINGS 未受信) の場合に自側 config 値を送信クレジット・ストリーム上限として使う:
  - データ: `max_data_local` / `peer_max_stream_data_uni` / `peer_max_stream_data_bidi_local` / `peer_max_stream_data_bidi_remote` (Section 6.5 の MUST 違反)
  - ストリーム数: `max_streams_bidi_local` / `max_streams_uni_local` (Section 6.7 の MUST 違反。`open_stream` は config 既定値 (各 100) までストリームを開けてしまう)
- 意図的フォールバックであることはコメントに明記されているが、仕様上の MUST 違反である

## 設計方針

- 対向 SETTINGS が 0 (または未受信) の場合は送信クレジット 0・ストリーム上限 0 (カプセル到着待ち) として扱う
- クレジット 0 の間に送信を試みた場合の挙動は、既存のフロー制御ガード (send_stream_data の超過時は `report_flow_control_error` → WT_FLOW_CONTROL_ERROR でセッションクローズ) による仕様違反の検知として位置づける。アプリは WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS カプセルの受信後に送信・開設する
- フォールバックが必要な相互運用上の根拠は確認できていない。削除後の実ブラウザ (Chromium / WebKit) との接続は、変更後の ブラウザ E2E (tests/browser) が全て通ることで担保する
- 変更対象: `src/bindings/webtransport_h2.cpp` (apply_peer_initial_flow_control) / テスト / CHANGES.md (## develop への [FIX])

## 完了条件

- 対向 SETTINGS が 0 (または未受信) のセッションで、WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS カプセルの受信まで、データ送信・ストリーム開設が行われない (データ送信を試みた場合は既存のフロー制御ガードで WT_FLOW_CONTROL_ERROR、ストリーム開設を試みた場合は open_stream が -1 を返して失敗する)
- 対向 SETTINGS が非 0 の場合、従来どおり非 0 の値で送信クレジット・ストリーム上限を設定する
- テストが追加され、全テスト (ブラウザ E2E を含む) が通る

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::apply_peer_initial_flow_control` から自側 config 値へのフォールバックを除去し、対向 SETTINGS が 0 (draft-15 Section 11.2 の既定値) の場合の送信クレジット・ストリーム上限を 0 とした (データクレジット 4 種 + ストリーム数 2 種 + ストリームデータ 3 種の全 9 箇所。Section 6.5 / 6.6 / 6.7 の MUST 準拠)
- テスト: `tests/test_webtransport_h2_initial_flow_control_fallback.py` (0 クレジットで open_stream が -1・カプセル受信で前進するラウンドトリップ / 単方向ストリーム上限の 0 と前進 / データクレジット 0 での送信試行が WT_FLOW_CONTROL_ERROR)。`tests/test_webtransport_h2_flow_control_capsule.py` の既存テストをフォールバック前提の名称から現状の挙動を説明する名称に変更し、DOCSTRING を更新した。`tests/test_webtransport_h2_close_session.py` のフォールバック前提コメントも更新した
