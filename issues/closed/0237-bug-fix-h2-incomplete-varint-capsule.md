# h2 で不完全な可変長整数を持つカプセルが無検証で読み捨てられる

- Created: 2026-09-18
- Completed: 2026-09-20
- Branch: feature/fix-h2-incomplete-varint-capsule
- Polished: 2026-09-20

## 目的

WebTransport over HTTP/2 の受信ハンドラは、カプセルのペイロードに含まれる可変長整数を完結にデコードできないと、何もせず return する。この経路では検証が一切行われず、セッションも閉じないため、非コンプライアントなピアがペイロードの末尾を欠けさせたカプセルを送るだけで、方向検証・ストリーム状態検証・二重受信検証を丸ごと回避できる。

draft-ietf-webtrans-http2-15 自身はペイロード内部が不正な場合の扱いを定めていないが、同 draft はカプセルのフレーミングを RFC 9297 (Capsule Protocol) に委ねている。RFC 9297 Section 3.3 は「カプセルのペイロードは識別フィールドを正確に含まなければならず、識別フィールドの終端に達していないペイロードは malformed / incomplete な HTTP メッセージとして扱う」ことを MUST とし、HTTP/2 では RFC 9113 Section 8.1.1 の扱い (PROTOCOL_ERROR のストリームエラー) を参照している。本実装はこの MUST を満たしていない。

## 現状

- `H2Session::process_capsules` は Type と Length を読んでペイロード長の範囲だけを確定し、ペイロード内部の可変長整数はハンドラに委ねる。そのため Length が正しくても内部の可変長整数が不完全なカプセルはハンドラに到達する
- ペイロードの可変長整数を読む受信ハンドラは 8 個あり、いずれも `decode_varint` の失敗で何もせず return する。エラーイベントの送出も無い
  - `H2Session::handle_wt_stream` (WT_STREAM / WT_STREAM_FIN。空ペイロードは Stream ID すら含まれないため、無視せずプロトコル違反として扱う対象に含める)
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
- `tests/` にこの経路を検証するテストは無い (ペイロードが未到達なカプセルの蓄積を検証するテストはあるが、Length が整合し内部の可変長整数だけが不完全な入力は無い)

## 設計方針

- 不完全なペイロードは RFC 9297 Section 3.3 のプロトコル違反として扱い、PROTOCOL_ERROR (RFC 9113 Section 7) のストリームエラー (RST_STREAM) を送出する (方針は本 issue で確定する)
  - 根拠 1: RFC 9297 Section 3.3 は「識別フィールドの終端に達していないペイロード」を malformed / incomplete な HTTP メッセージとして扱うことを MUST とし、HTTP/2 では RFC 9113 Section 8.1.1 (malformed なメッセージは PROTOCOL_ERROR のストリームエラー) を参照している
  - 根拠 2: draft-15 Section 3.4 は「HTTP/2 ストリームのリセットはセッションを終了させる (アプリケーションシグナルは送らない)」としており、RST_STREAM は正規のセッション終了経路である
  - 根拠 3: 既存の WT_ERROR (`report_wt_error` の 5 箇所) は draft が明示的に WT_ERROR を要求する入力 (Application Protocol Error Code の 0xffffffff 超、WT_CLOSE_SESSION のメッセージ長・UTF-8) と実装独自のペイロード上限超過に使っており、RFC 9297 が扱いを定めるペイロード不正とは根拠が異なる
- 読み出しは共通ヘルパに寄せる。`decode_varint` の失敗時にストリームをリセットして失敗を返すヘルパを追加し、上記 8 ハンドラすべてで使う (ハンドラごとの実装のばらつきを無くし、ハンドラ追加時の再発を防ぐ)
- `H2Session::handle_wt_stream` の空ペイロード (`length == 0`) も他のハンドラと同じくプロトコル違反として扱う (Stream ID すら含まれず、RFC 9297 Section 3.3 の「識別フィールドの終端に達していないペイロード」に該当する)。draft-15 Section 6.4 が無視を MAY とする「空の WT_STREAM」は Stream ID のみでデータを持たないカプセルであり、こちらは従来どおりイベントを発火せずストリームを暗黙作成する (0236 の WebKit 相互運用の判断)
- カプセルの Type / Length 自体が不完全な場合は従来どおり後続のバイト列を待って蓄積する (RFC 9297 Section 3.2 のフレーミング。この経路は変更しない)
- サーバーが受理前に蓄積したカプセルは accept_session で遅延処理されるため、この経路でも同じリセットになる。リセット後は初期クレジット (WT_MAX_DATA / WT_MAX_STREAMS) を送出しない (accept_session は is_terminated を見て中断する)
- 検証の順序は変えない。前置フィールドだけが読めて後続フィールドが読めない入力はカプセルとして不正なためプロトコル違反とする (WT_STREAM_STATE_ERROR は全フィールドをデコードできたカプセルの検証結果として送出する)

## 完了条件

- 上記 8 ハンドラすべてで、不完全なペイロードのカプセルが無言で読み捨てられず、PROTOCOL_ERROR の RST_STREAM が送出されてセッションが終了する。アプリケーションシグナル (WT_CLOSE_SESSION) は送出せず、終了は SessionClosed (error_code は HTTP/2 の PROTOCOL_ERROR) で観測できる。Error イベントは送出しない (RST_STREAM の submit 自体が失敗した場合のみ、終了を観測できないため Error イベントで通知する)
- ピア側でもストリームのリセットによりセッションが終了する
- 空ペイロード (Length 0) の WT_STREAM も他のハンドラと同じくリセットされる
- Stream ID のみでデータを持たない WT_STREAM の既存の挙動 (イベントを発火せずストリームを暗黙作成し、セッションを維持) が変わらない
- カプセルの Type / Length / ペイロードが途中の場合は蓄積して後続を待ち、揃った時点で処理される (既存の挙動の回帰ピン)
- 受理前に蓄積した不完全なカプセルも、accept_session の遅延処理で同じくリセットされる (初期クレジットは送出しない)
- 上記を検証するテストが追加され、全テストが通過する
  - テストは低レベル `h2.Session` で検証する。WT_ERROR や WT_STREAM_STATE_ERROR は高レベル `Client` / `Server` の `on_error` には渡らず (`WT_FLOW_CONTROL_ERROR` のみが渡る)、HTTP/2 のストリームエラーも低レベルで観測する

## 対象外

- 識別フィールドの後に余分なバイトがあるカプセルの検証 (RFC 9297 Section 3.3 の同じ MUST のもう一方。追跡 issue は未起票)
- `WT_DATA_BLOCKED` (draft-15 Section 6.8) は値の検証も読み出しも行っていない (追跡 issue は未起票)
- 固定長フィールドのみが不完全なカプセルの検証 (`H2Session::handle_wt_close_session` の Application Error Code 4 バイト未満。追跡 issue は未起票)
- 未知の Capsule Type の読み捨て (RFC 9297 Section 3.2 のとおり)
- ペイロードが揃ったカプセルの値の意味論 (単調性・ストリーム状態など) は既存の検証のまま
- 既存の `report_wt_error` / `report_stream_state_error` の呼び出しを RST_STREAM へ変更すること (draft-15 が明示するエラー経路。本 issue はペイロード不正の経路だけを対象とする)
- クリーンな END_STREAM の直前に切り詰められたカプセルの検証 (RFC 9297 Section 3.3 第 3 段落の MUST。END_STREAM ではセッションが終了するため実害は限定的。追跡 issue は未起票)
- テストのカプセル種別定数の重複 (0239 で集約する) と、RST_STREAM フレーム生成ヘルパの重複 (tests/conftest.py への集約は 0221 / 0239 の対象。本 issue ではテストファイル内のローカルヘルパに留める)

## 解決方法

- `src/bindings/webtransport_h2.cpp` に `H2Session::read_capsule_varint` を追加し、ペイロードの可変長整数のデコード失敗時に `H2Session::reset_stream_for_malformed_capsule` で PROTOCOL_ERROR の RST_STREAM を送出する。ペイロードの可変長整数を読む 8 ハンドラの 13 箇所をすべてこのヘルパに置き換えた
- `H2Session::handle_wt_stream` の `length == 0` の早期 return を削除し、空ペイロードも同じ経路 (プロトコル違反) にした。draft-15 Section 6.4 が無視を MAY とする empty WT_STREAM (Stream ID のみ) の扱いは変えていない
- `reset_stream_for_malformed_capsule` は `is_terminated` / `is_established` を落として以後のカプセル処理と送受信を止め、`nghttp2_submit_rst_stream(..., NGHTTP2_PROTOCOL_ERROR)` を送出する。submit に失敗した場合は終了を観測できないため Error イベントを push する (error_code は nghttp2 エラーコードの絶対値)。`close_session` は使わない (WT_CLOSE_SESSION はアプリケーションシグナルであり、draft-15 Section 3.4 のリセットによる終了と意味が異なる)。カプセルバッファは `process_capsules` のループが `is_terminated` を見て破棄する
- `tests/test_webtransport_h2_incomplete_capsule_payload.py` を追加し、次の 27 件を検証する
  - 不完全なペイロード (2 / 4 / 8 バイト varint の途中、空ペイロード) を 8 ハンドラの 13 デコード箇所すべてに注入し、PROTOCOL_ERROR の RST_STREAM の送出・WT_CLOSE_SESSION 不在・Error イベント不在・SessionClosed (error_code は HTTP/2 の PROTOCOL_ERROR)・ピア側のセッション終了を確認する (21 件)
  - カプセルの Type / Length / ペイロードが途中の場合は蓄積して待ち、揃った時点で処理されることを 3 つの分割位置で確認する (3 件)
  - Stream ID のみでデータを持たない WT_STREAM がイベントを発火せずストリームを暗黙作成し、セッションを維持すること (1 件)
  - 受理前に蓄積した不完全なカプセルが accept_session の遅延処理でリセットされ、初期クレジット (WT_MAX_DATA / WT_MAX_STREAMS) を送出しないこと (1 件)
- 実装前のテスト実行で、不完全ペイロードのケースが失敗する (無言 return のため RST_STREAM も SessionClosed も発生しない) ことを実測で確認した
- `H2Event::error_code` / `H2Session::is_terminated` / `read_capsule_varint` のコメントを今回の経路に合わせて更新した
- 全テストが通過する
