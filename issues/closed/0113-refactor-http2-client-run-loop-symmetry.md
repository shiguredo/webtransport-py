# HTTP/2 クライアントの run() に server と対称な is_closed() チェックを追加する

- Created: 2026-08-18
- Completed: 2026-08-21
- Branch: feature/refactor-http2-client-run-loop-symmetry
- Polished: 2026-08-21

## 目的

`webtransport.http2.Server._handle_client` (`src/webtransport/http2/server.py`) は各周回で `if connection.is_closed(): break` を実行するのに対し、`webtransport.http2.Client.run` (`src/webtransport/http2/client.py`) は同種のチェックを持たない。この対称性の欠如を埋め、`Client.run` にも `is_closed()` チェックを defensive check として追加する。

現行 nghttp2 の実装契約上、Python 側の任意バイト入力で `nghttp2_session_mem_recv` / `nghttp2_session_mem_send` を negative return させるのは困難で、実 production ハングを直接修正するわけではない。したがって本 issue は `bug-fix` ではなく `refactor` として扱う。

HTTP/3 側の高レベル `run()` が握りつぶされたエラーで終了できない件は 0107 (`HTTP/3 のプロトコルエラーが無音で握りつぶされる問題を修正する`) の完了条件「高レベル層の run() がハングせず終了する」で扱うため、本 issue の対象外とする。

## 現状

- `src/webtransport/http2/server.py` の `_handle_client` は各周回で `if connection.is_closed(): break` を実行し、低レベル `http2_low.Connection.is_closed()` が True になった時点でループを抜ける
- `src/webtransport/http2/client.py` の `Client.run` は次の 3 経路でしか `_running = False` を立てない。`http2_low.Connection.is_closed()` を確認する経路が無く、server との対称性を欠く:
  - `http2_low.EventType.GO_AWAY` イベント受信時
  - `Client._receive` の TCP EOF (`self._reader.read()` が空バイトを返す) 時
  - `Client.close` の同期経路 (最初の await より前に `self._running = False` を立てる)
- `src/bindings/http2.cpp` の `Http2Connection::receive` (`nghttp2_session_mem_recv` が負を返した分岐) と `Http2Connection::send` (`nghttp2_session_mem_send` が負を返した分岐) は `closed_ = true` を立てるがイベントを push しない。しかし nghttp2 の設計上、これらの負値は `NGHTTP2_ERR_CALLBACK_FAILURE` / `NGHTTP2_ERR_FLOODED` / `NGHTTP2_ERR_NOMEM` にほぼ限定される
- 通常のプロトコルエラー (RFC 9113 Section 5.5 の未知フレーム type は silently discard、Section 5.4.1 の Connection Error は GOAWAY 送出) は nghttp2 が内部で GOAWAY を queue して `mem_recv` は正常終了させるため、`GO_AWAY` イベントが発火して既存経路で `_running = False` になる
- 結果として、production HTTP/2 サーバー相手の通常運用ではこの経路で無限ループになることは稀 (FLOODED 相当の DoS 攻撃時等に限られる) だが、`Client.run` に `is_closed()` チェックが無いのは server との対称性を欠く実装上の欠陥

## 設計方針

- `Client.run` のメインループのイベント処理ループ (`while True: next_event()`) の直後、`await asyncio.sleep(0.01)` の前で `if self._connection.is_closed(): self._running = False` を追加する
- チェックの位置はサーバー側 `_handle_client` (line 220-221) に合わせ、低レベルが吐き出す最後のイベント (`GO_AWAY` 受信時に push される Http2EventType::GoAway 等) を取りこぼさないよう、イベント処理を通してから状態確認する
- 既存の GO_AWAY イベント経路・TCP EOF 経路・`close()` の同期経路はそのまま残す。`GO_AWAY` 受信時は `src/bindings/http2.cpp` の `on_frame_recv_callback` が Http2EventType::GoAway を push した直後に `closed_ = true` を立てるため、GO_AWAY イベント経路と `is_closed()` チェック経路の両方が立つ。どちらの経路で `_running = False` になっても副作用は無い (bool への `False` 代入は idempotent)
- エラーコード・エラーメッセージをアプリへ通知する API の追加は本 issue の対象外 (必要になれば別途 `add` カテゴリの issue を起票する)
- `src/bindings/http2.cpp` は変更しない (テスト専用ヘルパを bindings 側に追加する話は本 issue の対象外。将来別 issue で扱う)

## 完了条件

- `src/webtransport/http2/client.py` の `Client.run` メインループに `if self._connection.is_closed(): self._running = False` (もしくは同等の break) が追加され、`src/webtransport/http2/server.py:220-221` の `if connection.is_closed(): break` と対称になっている
- 既存の GO_AWAY 受信ケースと `close()` 呼び出しケースで `Client.run()` が引き続き終了することを回帰テストで確認する。追加した `is_closed()` チェックが既存経路の挙動を壊していないことの確認であり、`is_closed()` チェック単独の効果検証ではないことを承知の上で残す (単独検証には bindings 側にテスト専用ヘルパの追加が必要で、本 issue のスコープ外)
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する (`AGENTS.md`「テストはコメントを重視すること」)
- `AGENTS.md`「モックやスタブは絶対に利用しないこと」に従い、実際の `webtransport.http2.Server` と `webtransport.http2.Client` を組み合わせた e2e として書く

## 解決方法

- `src/webtransport/http2/client.py`: `Client.run` のイベント処理ループ (`while True: next_event()`) の直後、`await asyncio.sleep(0.01)` の前で `if self._connection.is_closed(): self._running = False` を追加した。`src/webtransport/http2/server.py` の `_handle_client` (`if connection.is_closed(): break`) と対称になる位置に配置した
- `tests/test_e2e_http2.py`: 次の 2 つの回帰テストを追加した:
  - `test_client_run_exits_on_close`: `Client.run()` 実行中に `close()` を呼び、run() が終了することを検証する
  - `test_client_run_exits_on_goaway_injection`: 低レベル `Connection.receive` に GOAWAY フレームのバイト列を注入し、`Client.run()` が終了することを検証する
- `CHANGES.md`: `## develop` の `### misc` に `[UPDATE] HTTP/2 クライアントの run() に server と対称な is_closed() チェックを追加する` を追加した
- テスト全 722 件パス、`ruff format` / `ruff check` / `ty check` はすべて通過

フレームエラー独立検証テストは、Python 側から nghttp2 の negative return 経路を誘発できないため本 issue では追加していない。bindings 側にテスト専用ヘルパを追加する話は別 issue で扱う
