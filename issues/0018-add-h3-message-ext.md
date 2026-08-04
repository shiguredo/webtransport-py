# nghttp3 の送信側拡張 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-04
- Branch: feature/add-h3-message-ext
- Polished: 2026-08-04

## 目的

HTTP/3 のトレーラ送信・1xx レスポンス・graceful shutdown (shutdown notice)・書き込み側シャットダウンを Python から行えるようにする。現在はトレーラや 1xx を扱う手段が無い。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection` は `submit_request` / `submit_response` / `send_data` / `reset_stream` / `close_stream` / `goaway` を公開しているが、トレーラ・1xx・shutdown notice は扱えない
- `goaway()` は `nghttp3_conn_shutdown` に相当するが、本家の推奨手順 (shutdown notice → RTT 待ち → shutdown) を踏んでいない
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_submit_trailers`: トレーラ送信 (ストリーム終端を担う)
  - `nghttp3_conn_submit_info`: 1xx レスポンス (Informational Response。サーバー専用)
  - `nghttp3_conn_submit_shutdown_notice`: graceful shutdown の開始通知 (GOAWAY 相当)
  - `nghttp3_conn_shutdown_stream_write`: ストリームの書き込み側シャットダウン (QUIC FIN ではなく、以降の書き込みを禁止する)

## 設計方針

- `Http3Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http3.Connection`)。変更対象は `src/bindings/http3.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_http3_message_ext.py`)。`src/webtransport/http3/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要。Python 側の公開名は nghttp3 の API 名をそのまま用いる (get_ / set_ / pri_ を含まない API のため)
- `submit_trailers` はストリーム ID とヘッダーを受け取り、`send_data(fin=True)` で本体を積んだ後、flush 前に呼ぶ。submit_trailers 自体が WRITE_END_STREAM フラグを立ててストリーム終端を担い、flush 時に read_data_cb が本体と EOF を返すと、DATA フレームの後に frq に積まれたトレーラ HEADERS が書き出される (nghttp3 の fill_outq は WRITE_END_STREAM フラグを参照せず frq を順に処理するため)。呼び出し順序の制約は 2 つ: 本体は fin=True で積む (fin=False では read_data_cb が EOF を返す経路がなくトレーラに到達しないため、「fin を立てずに送る」方式は採らない) / flush 前に呼ぶ (flush 後に呼ぶと WRITE_END_STREAM 済みのため NGHTTP3_ERR_INVALID_STATE になる)。C++ 実装では既存の `send_data` と同様に `nghttp3_conn_resume_stream` を呼んで READ_DATA_BLOCKED を解除する
- `submit_info` はストリーム ID とヘッダーを受け取り、最終レスポンス (submit_response) より前に呼ぶ (1xx は frq の書き出し順で最終レスポンスより先に送られる必要がある)。サーバー専用。nghttp3 は assert で conn->server を要求するが、プロジェクトは nghttp3 を Release ビルドするため assert は無効化されている (無効時は未定義動作になる)。C++ 側でサーバーのみガードする
- `submit_shutdown_notice` は独立メソッドとして追加する (goaway() のセマンティクスは変更しない。nghttp3_conn_shutdown は goaway() の現行実装であり、0017 の `drained` 検証がこれに依存している。shutdown notice は GOAWAY_QUEUED のみ立て SHUTDOWN_COMMENCED は立てないため、drained は true にならない)。サーバー専用とする (クライアントで呼ぶと PUSH ID 版 GOAWAY が送られる。RFC 9114 5.2 節はクライアントの GOAWAY が push ID を運ぶと定義しており、これは push の制限であってサーバーの graceful shutdown の意味を持たない。libnghttp3 は Server Push を実装していないため実質的効果もない)。shutdown notice と shutdown (goaway()) の呼び出し順序は、notice → shutdown の順とする。逆順は GOAWAY ID の単調減少 (RFC 9114 5.2 節の MUST NOT) に違反し、Release ビルドでは assert が無効化されているため、違反 GOAWAY がそのまま送信されてピアから H3_ID_ERROR で切断される。C++ 側で goaway() 呼び出し済みかどうかを追跡し、goaway() 後の submit_shutdown_notice は False を返す
- `shutdown_stream_write` はストリーム ID を引数に取り、書き込み側を閉じる (QUIC FIN ではなく、それ以降の書き込みを禁止する。nghttp3 の意味は block_stream と同様だが unblock_stream では解除できない)。nghttp3 の実装は SHUT_WR フラグを立ててスケジューラから外すだけで、shutdown 後の resume_stream では再スケジュールされて送信され得るため、C++ 側で shutdown 済みストリーム ID を追跡し、shutdown 後の `send_data` は no-op とする。void のため戻り値なし
- int を返す API (`submit_trailers` / `submit_info` / `submit_shutdown_notice`) は成功で True / 失敗で False を返す。nghttp3 の assert 条件 (Release ビルドでは assert 無効のため未定義動作を防ぐ) を避けるため C++ 側でガードする。assert の条件は API ごとに異なる: `submit_trailers` は tx.qenc (QPACK) のみ / `submit_info` は conn->server と tx.qenc / `submit_shutdown_notice` は tx.ctrl (制御ストリーム) のみ。ガード時は False を返す (既存の `submit_request` と同じ。`goaway()` は void のため no-op とする)
- WebTransport (H3Session) には追加しない (トレーラ・1xx は HTTP メッセージの概念であり WebTransport では使わない。shutdown_stream_write はストリーム制御 API で性質が異なり、WebTransport への追加が必要になれば別途検討する)。この issue では Http3Connection のみに追加する
- 0017 / 0019 / 0024 (http3.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python からトレーラを送信できる (send_data(fin=True) で本体を積み、flush 前に submit_trailers を呼んで終端する。受信側の確認は既存のヘッダー受信イベントで行う。トレーラも本体ヘッダーと同じ HEADERS イベントとして積まれるため、本体レスポンスの後 (DATA の後) に届いたものをトレーラと判断する)
- Python から 1xx レスポンスを送信できる (サーバーのみ。最終レスポンスより先に submit_info で送る。受信側の確認は既存のヘッダー受信イベントで :status の内容により 1xx と判断する。イベント種別上、1xx と最終レスポンスは区別されない点に注意。テストで使う 1xx は 100 か 103 にすること (nghttp3 は 101 Switching Protocols を拒否する)。1xx を送った後は必ず最終レスポンスを送ること (nghttp3 は 1xx の後に最終レスポンスが無いとエラーにする))
- Python から graceful shutdown (shutdown notice) を開始できる (goaway() のセマンティクスが変更されていないことを確認する。受信側の確認は既存の GoAway イベントで行う)
- Python からストリームの書き込み側をシャットダウンできる (shutdown 後の `send_data` は no-op となり送出されないことを確認する。0017 実装済みなら `stream_writable` が false になることも確認する)
- モックなしのテストで、各 API が動作することを確認する (Http3Connection は低レベル受け渡し構成 (0017 と同様の `_pump` 方式) でテストする。0017 実装済みなら流用し、未実装なら 0013 と同様の構成を新規に構築する)

## 解決方法

`src/bindings/http3.cpp` / `.h` の `Http3Connection` に 4 つの送信側拡張 API を追加し、nanobind で公開した。

- `submit_trailers` (nghttp3_conn_submit_trailers): ストリーム ID とヘッダーを受け取り、トレーラを送信する。呼び出し自体がストリーム終端 (WRITE_END_STREAM) を担う。flush で fin が送信処理された後の呼び出しは NGHTTP3_ERR_INVALID_STATE になり False を返す
- `submit_info` (nghttp3_conn_submit_info): 1xx レスポンスを送信する。サーバー専用 (is_server_ ガード)。QPACK ストリーム未バインド時は False
- `submit_shutdown_notice` (nghttp3_conn_submit_shutdown_notice): graceful shutdown の開始通知 (GOAWAY 相当) を送信する。サーバー専用。制御ストリーム未バインド・goaway() 済み・送信済みの場合は False (GOAWAY ID の単調減少と重複送信の防止)
- `shutdown_stream_write` (nghttp3_conn_shutdown_stream_write): ストリームの書き込み側をシャットダウンする。shutdown 済みストリーム ID を C++ 側で追跡し、shutdown 後の `send_data` は no-op、`submit_trailers` は False を返す

テストは `tests/test_http3_message_ext.py` に追加した (15 件)。低レベル受け渡し構成 (`_pump` / `_create_connection_pair`) は 0017 と同様に構築し、トレーラ・1xx・shutdown notice・書き込み側シャットダウンの各動作をモックなしで検証した。GOAWAY ID の単調減少 ((1<<62)-4 → 0) と drained の遷移も確認している。
