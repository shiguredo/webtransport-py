# WebTransport over HTTP/2 のクライアントが 2xx 非 200 応答をセッション確立として扱わない問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-h2-client-2xx-response
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http2-15 Section 3.2「A WebTransport session is established when the server sends a 2xx response」に反し、クライアントが 200 応答のみを確立として扱う問題を修正する。201 等の 2xx 応答では connect が SESSION_READY を待ち続けてハングする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` は `:status` が "200" の場合のみ `is_success` としている
- 201 等の 2xx 応答では is_success にならず、`wt_sessions_` のセッションエントリも削除されない (非 2xx 分岐にも該当しない) ため、高レベル層 `src/webtransport/h2/client.py` の `Client.connect` は SESSION_READY を待ち続けてブロックする (既に client.py 内のコメントに既知の制約として明記されている)
- 201 応答の挙動を「is_established = false のまま残留する」前提でピン留めしている既存テストがある: `tests/test_webtransport_h2_reject_session.py` の `test_client_response_201_session_kept` / `tests/test_webtransport_h2_end_stream.py` の `test_end_stream_201_no_termination`

## 設計方針

- `:status` の先頭文字が '2' であることを確立条件とし、現行の is_success 分岐 (is_established の適用・SESSION_READY イベントの発火・初期フロー制御の適用) を 2xx 全般に拡張する
- H3 側 (`end_headers_cb`) は「2xx 全般を確立」としながら「SESSION_READY は 200 のみ」という二層制約を持つが、これは H3 の高レベル `Client.connect` が SESSION_READY を待たないから成立するものであり、H2 の高レベル `Client.connect` は SESSION_READY を待つため、H2 側では 2xx 全般で SESSION_READY を発火させる (H3 と非対称になるが、H2 側の connect 契約に合わせる。H3 側の 2xx 全般化は issue 0112 が担当し、対応後は非対称は解消される)
- スコープ限定: 1xx 中間応答の後の 2xx (例: 103 を挟んだ 201) は既知の制約 (NGHTTP2_HCAT_HEADERS の振り分けによるもの) であり本 issue の対象外とする
- 変更対象: `src/bindings/webtransport_h2.cpp` (on_frame_recv_callback の is_success 判定と関連コメント) / 高レベル層の変更が必要なら `src/webtransport/h2/client.py` / 既存の 201 ピン留めテストの更新 / テスト追加 / CHANGES.md (## develop への [FIX])

## 完了条件

- 201 等の 2xx 応答でセッションが確立として扱われ (is_established / SESSION_READY)、connect が成功する
- 既存の 201 ピン留めテスト (test_client_response_201_session_kept / test_end_stream_201_no_termination) を新しい契約に合わせて更新する
- 2xx 非 200 (200 以外の 2xx) のテストが追加され、実行される
- 全テストが通る

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` で、`:status` の先頭文字が '2' であることを確立条件 (`is_success`) とした (draft-15 Section 3.2 の MUST。200 のみの判定を 2xx 全般へ拡張。既存の is_success 分岐はそのまま 2xx 全般で is_established / SESSION_READY / 初期フロー制御カプセル送出を実行する)
- 高レベル層は変更不要 (bindings が SESSION_READY を 2xx 全般で発火するため、既存の connect の SESSION_READY 待ちで 201 等でも return True となる)
- テスト: `test_webtransport_h2_reject_session.py` (201 応答の確立・イベント順序 (SESSION_READY → SESSION_CLOSED) のピン / END_STREAM なしの 201 をワイヤ注入した確立後のセッション機能 (open_stream / send_datagram) / 200 通常経路の回帰ピン docstring 更新) と `test_webtransport_h2_end_stream.py` (201 セッションの END_STREAM 終了検知)。1xx (100-199) を挟んだ応答の最終 2xx は既知の制約としてスコープ外 (変更なし)
