# h2 で不完全な可変長整数を持つカプセルが無検証で読み捨てられる

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-incomplete-varint-capsule
- Polished: 2026-09-20

## 目的

WebTransport over HTTP/2 の受信ハンドラは、カプセルのペイロードに含まれる可変長整数を完結にデコードできないと、何もせず return する。この経路では検証が一切行われず、セッションも閉じないため、非コンプライアントなピアがペイロードの末尾を欠けさせたカプセルを送るだけで、方向検証・ストリーム状態検証・二重受信検証を丸ごと回避できる。

draft-ietf-webtrans-http2-15 はペイロード内部が不正な場合の扱いを定めていない。本実装は非準拠なカプセル入力を既に WT_ERROR (`H2Session::report_wt_error`。draft-15 Section 3.4 の generic なセッションエラー) で扱っており (ペイロード上限超過、Application Protocol Error Code の 0xffffffff 超、WT_CLOSE_SESSION のメッセージ長超過と UTF-8 不正)、可変長整数が不完全なペイロードだけがこの方針から外れている。

## 現状

- `H2Session::process_capsules` は Type と Length を読んでペイロード長の範囲だけを確定し、ペイロード内部の可変長整数はハンドラに委ねる。そのため Length が正しくても内部の可変長整数が不完全なカプセルはハンドラに到達する
- ペイロードの可変長整数を読む受信ハンドラは 8 個あり、いずれも `decode_varint` の失敗で何もせず return する。エラーイベントの送出も無い
  - `H2Session::handle_wt_stream` (WT_STREAM / WT_STREAM_FIN。空ペイロードはデコードの前に無視する)
  - `H2Session::handle_wt_reset_stream` (Stream ID / Application Protocol Error Code / Reliable Size)
  - `H2Session::handle_wt_stop_sending` (Stream ID / Application Protocol Error Code)
  - `H2Session::handle_wt_max_data` (Maximum Data)
  - `H2Session::handle_wt_max_stream_data` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_max_streams` (Maximum Streams)
  - `H2Session::handle_wt_stream_data_blocked` (Stream ID / Maximum Stream Data)
  - `H2Session::handle_wt_streams_blocked` (Maximum Streams)
- 対象ハンドラの方向検証・ストリーム状態検証・二重受信検証は、いずれも可変長整数のデコードより後段にある。不完全なペイロードではここへ到達しない (実測: WT_RESET_STREAM / WT_STOP_SENDING / WT_MAX_STREAM_DATA / WT_STREAM_DATA_BLOCKED の 4 種で、空ペイロード・Stream ID のみ・途中で切れた可変長整数のいずれでも、イベントは 1 件も発火せず、セッションは維持され、WT_CLOSE_SESSION も送出されない)
- `H2Session::receive` の戻り値は HTTP/2 が消費したバイト数であり、カプセルを処理したかどうかを表さない。不完全なペイロードのカプセルも受信ウィンドウの消費 (`H2Session::process_capsule` の `consume_recv_bytes`) だけが行われ、アプリには何も通知されない
- 「無言で受理される」ではなく「無言で読み捨てられる」が実測に合う (ペイロードは検証されず、状態も変化しない)
- `tests/` にこの経路を検証するテストは無い (Length が未完成なカプセルの蓄積を検証するテストはあるが、Length が整合し内部の可変長整数だけが不完全な入力は無い)

## 設計方針

- 不完全なペイロードは WT_ERROR のセッションエラーとして閉じる (方針は本 issue で確定する)
  - 根拠 1: 本実装はカプセルのエラー経路で既に一貫して WT_ERROR を使っている (`report_wt_error` の呼び出し 5 箇所。うち 4 箇所は draft の MUST 違反、残る 1 箇所は実装独自のペイロード上限超過)
  - 根拠 2: draft に規定が無い以上、無言 return を維持する積極的な理由が無い (維持の根拠として想定していた「他実装が不完全なペイロードを送る」事例は見つかっていない)
  - 根拠 3: 黙って捨てると検証が入力次第で回避でき、ピアの状態機械と食い違ったままセッションが継続する
- 読み出しは共通ヘルパに寄せる。`decode_varint` の失敗時に `report_wt_error` を呼んで失敗を返すヘルパを追加し、上記 8 ハンドラすべてで使う (ハンドラごとの実装のばらつきを無くし、ハンドラ追加時の再発を防ぐ)
- `H2Session::handle_wt_stream` の空ペイロード (`length == 0`) の無視は維持する。draft-15 Section 6.4 はストリームを開閉しない空の WT_STREAM を session error として扱うことを MAY としており、0236 の WebKit 相互運用の判断と揃っているため、空ペイロードは「不完全なペイロード」に含めない
- 検証の順序は変えない。前置フィールドだけが読めて後続フィールドが読めない入力はカプセルとして不正なため WT_ERROR とする (WT_STREAM_STATE_ERROR は全フィールドをデコードできたカプセルの検証結果として送出する)
- 対象は可変長整数の不完全に限る。固定長フィールドの検証 (例: `H2Session::handle_wt_close_session` の Application Error Code 4 バイト未満) は別の対応とする

## 完了条件

- 上記 8 ハンドラすべてで、不完全なペイロードのカプセルが無言で読み捨てられず、`report_wt_error` による WT_ERROR のセッションエラーになる (Error イベントが `error_code == WtErrorCode.WT_ERROR.value` で push され、WT_CLOSE_SESSION が送出され、セッションが終了する)
- `H2Session::handle_wt_stream` の空ペイロードの既存の挙動 (無視してセッションを維持) が変わらない
- 上記を検証するテストが追加され、全テストが通過する
  - テストは低レベル `h2.Session` で検証する。WT_ERROR は高レベル `Client` / `Server` の `on_error` には渡らない (`WT_FLOW_CONTROL_ERROR` のみが渡る) ため、低レベルで Error イベントとセッション終了を観測する

## 対象外

- 固定長フィールドのみが不完全なカプセルの検証 (`H2Session::handle_wt_close_session` の Application Error Code 4 バイト未満。追跡 issue は未起票)
- 未知の Capsule Type の読み捨て (カプセルプロトコルの仕様どおり)
- ペイロードが揃ったカプセルの値の意味論 (単調性・ストリーム状態など) は既存の検証のまま
