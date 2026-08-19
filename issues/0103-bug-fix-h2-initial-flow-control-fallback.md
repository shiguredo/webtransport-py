# WebTransport over HTTP/2 の初期フロー制御 0 フォールバックが広告制限を超える問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-initial-flow-control-fallback
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.5 の MUST「WT_STREAM のデータ合計は受信者が広告した制限を超えてはならない」に反し、対向 SETTINGS が 0 (仕様デフォルト) の場合に自側 config 値を送信クレジットとして使う問題を修正する。仕様では 0 は「WT_MAX_DATA カプセル到着まで送信不可」を意味するため、現在のフォールバックは広告制限を超えた送信となり、コンプライアントなピアから WT_FLOW_CONTROL_ERROR を受ける。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::apply_peer_initial_flow_control` は、対向 SETTINGS の値が 0 の場合に自側 config 値 (`config_.wt_initial_max_data` 等) を送信クレジットとして使う
- これは受信者が広告した 0 の制限を超えて送信することに等しく、Section 6.5 の MUST 違反
- 意図的フォールバックであることはコメントに明記されているが、仕様上の MUST 違反である

## 設計方針

- 対向 SETTINGS が 0 の場合は送信クレジット 0 (カプセル到着待ち) として扱う
- フォールバックが本当に必要か (相互運用上の根拠) を確認し、必要な場合は仕様上の位置づけを整理する

## 完了条件

- 対向 SETTINGS が 0 のセッションで、WT_MAX_DATA / WT_MAX_STREAM_DATA カプセル到着までデータ送信がブロックされる
- テストが追加される
