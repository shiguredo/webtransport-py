# WebTransport over HTTP/2 で不完全な可変長整数を持つカプセルが無言で受理される

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-incomplete-varint-capsule
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 の受信ハンドラは、ペイロードの可変長整数を完全にデコードできなければ何もせず return する。この経路ではカプセルの方向検証もストリーム状態の検証も行われず、セッションも閉じない。そのため非コンプライアントなピアが、ペイロードの末尾を欠けさせた状態カプセルを送るだけで検証を素通りできる。

draft-ietf-webtrans-http2-15 はペイロード不正時の扱いを定めていないが、黙って受理すると Section 6.2 / 6.4 / 6.6 / 6.9 の MUST を満たせない入力が生じる。

## 現状

- `H2Session::process_capsules` は Type と Length を読んでペイロード長の範囲だけを確定し、ペイロード内部の可変長整数はハンドラに委ねる。そのため Length が正しくても内部の varint が不完全なカプセルはハンドラに到達する
- `H2Session::handle_wt_reset_stream` / `H2Session::handle_wt_stop_sending` / `H2Session::handle_wt_max_stream_data` / `H2Session::handle_wt_stream_data_blocked` はいずれも `decode_varint` の失敗で何もせず return する。エラーイベントも送出も無い
- `H2Session::receive` の戻り値は HTTP/2 が消費したバイト数であり、カプセルを処理したかどうかを表さない。呼び出し側は受理されたのか読み捨てられたのかを区別できない
- 空ペイロード、Stream ID のみのペイロード、途中で切れた可変長整数を持つペイロードのいずれでも、セッションは維持されたまま何も起きないことを実測で確認している (受信 4 ハンドラ共通)
- `tests/` にこの経路を検証するテストが無い

## 設計方針

- 不完全なペイロードの扱いを 4 ハンドラで統一する。個別のハンドラに散らすと 5 つ目のハンドラ追加時に同じ穴が再来する
- 扱いの候補は次の 2 つ。draft に規定が無いため、既存の `H2Session::report_wt_error` (Section 3.4 の generic な WT_ERROR) の使い方と整合する範囲で決める
  - 可変長整数の読み出しを共通ヘルパに寄せ、失敗時は `H2Session::report_wt_error` で WT_ERROR のセッションエラーにする (不正なカプセルを黙って捨てない)
  - 無言 return を維持しつつ、既知の制約としてコメントに明記する (相互運用を優先する場合)
- どちらを選ぶかは、他実装が不完全なペイロードを送り得るかを含めて判断する。判断材料を issue に記録してから実装する

## 完了条件

- 不完全な可変長整数を持つペイロードのカプセルが無言で受理されない (選んだ方針どおりの挙動になる)
- 4 ハンドラで挙動が統一されている
- 上記を検証するテストが追加され、全テストが通過する
