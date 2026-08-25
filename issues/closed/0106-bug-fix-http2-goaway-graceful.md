# HTTP/2 の GOAWAY 受信で進行中ストリームの処理が止まる問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-http2-goaway-graceful
- Polished: 2026-08-24

## 目的

RFC 9113 Section 6.8 が定義する GOAWAY の graceful shutdown (既存ストリームの処理を完了させながら新規ストリーム受付を止める。受信者は GOAWAY 受信後に新規ストリームを開けず、その接続が閉じられるまで既存ストリームを処理し続ける) に反し、GOAWAY 受信直後に接続が closed になり、進行中ストリームのレスポンス flush が止まってデータが失われる問題を修正する。

## 現状

- `src/bindings/http2.cpp` の `on_frame_recv_callback` は NGHTTP2_GOAWAY 分岐で `closed_ = true` にし、以後 `receive()` は 0 を返し `send()` は nullopt を返す
- 送信キューに積んだレスポンス (HEADERS + DATA) が GOAWAY 受信後に一切 flush されず、データが失われる
- 高レベル層 (`src/webtransport/http2/client.py` / `server.py` の run()) は GO_AWAY イベント受信で即終了するため、エンドツーエンドでも graceful shutdown にならない
- この挙動を仕様として固定している既存テストがある: `tests/test_http2_message_ext.py` の `test_http2_closed_connection_guards` / `tests/test_http2_session_state.py` (サーバーが GOAWAY 受信で閉鎖扱いになる getter テスト) / `tests/test_http2_session_control.py` (GOAWAY 受信後の閉鎖状態・2 枚目のフレーム未処理) / `tests/test_e2e_http2.py` の `test_client_run_exits_on_goaway_injection` (docstring が closed_ = true に依存)

## 設計方針

- **bindings 層**: NGHTTP2_GOAWAY 分岐で `closed_ = true` を立てず、`goaway_received_` フラグ (新規ストリーム開始の抑止にのみ使用) を立てる。以後も既存ストリームの送受信と送信キュー flush を続行する
- **新規ストリームの抑止範囲**: GOAWAY フレーム自体に種類 (graceful / 即時終了) の区別はなく、受信側はどちらでも同じ扱いとする。`goaway_received_` ガードを (a) クライアントの `submit_request`、(b) サーバーの `submit_push_promise` に追加する (nghttp2 が GOAWAY 受信後に新規リクエストを拒否する場合も、ガードを追加して契約を自前で保証する)
- **高レベル層**: `client.py` / `server.py` の GO_AWAY ハンドラを「即終了」から「既存ストリームの処理を完了させ、接続が閉じられるまで run() を継続する」に変更する。接続終了の判定は従来どおり is_closed() (または接続イベント) による
- 変更対象: `src/bindings/http2.cpp` / `http2.h` (goaway_received_ 追加と GOAWAY 分岐変更) / `src/webtransport/http2/client.py` / `server.py` (GO_AWAY ハンドラ) / 既存テストの更新 (上記 4 つ) / テスト追加 / CHANGES.md (## develop への [FIX])

## 完了条件

- GOAWAY 受信後も進行中ストリームのデータ処理と送信 flush が完了する (低レベルでは receive() / send() が closed_ 起因の 0 / nullopt にならない。エンドツーエンドで run() が既存ストリームの処理を完了する)
- GOAWAY 受信後に新規ストリームの開始が抑止される (クライアントの submit_request / サーバーの submit_push_promise の goaway_received_ ガード)
- 既存の closed_ 仕様テスト (test_http2_message_ext.py / test_http2_session_state.py / test_http2_session_control.py / test_e2e_http2.py の 5 本) を新しい挙動に合わせて更新する
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加され、全テストが通る

## 解決方法

- `src/bindings/http2.cpp` / `http2.h`: NGHTTP2_GOAWAY 分岐で `closed_ = true` を立てず `goaway_received_ = true` とする (RFC 9113 Section 6.8 の graceful shutdown)。`submit_request` / `submit_push_promise` に `goaway_received_` ガードを追加し、新規ストリームの開始を抑止する (nghttp2 は submit 時に拒否せず送信時に silent 破棄するため自前ガードが必要)
- `src/webtransport/http2/client.py` / `server.py`: GO_AWAY イベントのハンドラを「即終了」から「継続」に変更する (接続終了はピアの接続クローズと is_closed() で検知)
- 既存テスト 5 本を新しい挙動に合わせて更新し、`test_http2_goaway_after_response_delivered` (GOAWAY 受信後に進行中ストリームのレスポンス HEADERS + DATA がピアで受信されること) と e2e の継続テストを追加した
