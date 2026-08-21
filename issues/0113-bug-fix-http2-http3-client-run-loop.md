# HTTP/2 クライアントの run() が接続クローズ後に永久ループする問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-client-run-loop
- Polished: 2026-08-21

## 目的

高レベル `webtransport.http2.Client.run()` が、低レベル `http2_low.Connection` が `is_closed() == True` に遷移しても、それが GO_AWAY 受信・TCP EOF・`close()` 呼び出しのいずれの経路でもない場合には終了せず、アイドルサイクルを永久に回し続ける問題を修正する。具体的には、`nghttp2_session_mem_recv` / `nghttp2_session_mem_send` がエラーを返して低レベルが自主クローズしたケース (不正フレーム受信等) で、アプリ側の `Client.run()` タスクがハングする。

HTTP/3 側の高レベル `run()` が握りつぶされたエラーで終了できない件は 0107 (`HTTP/3 のプロトコルエラーが無音で握りつぶされる問題を修正する`) の完了条件「高レベル層の run() がハングせず終了する」で扱うため、本 issue の対象外とする。

## 現状

- `src/webtransport/http2/client.py` の `Client.run` は、`http2_low.EventType.GO_AWAY` イベントを受けたときに `_running = False` を立てる
- `Client._receive` は TCP EOF (`self._reader.read()` が空バイトを返す) 時に `_running = False` を立てる
- `Client.close` は同期的に (最初の await より前に) `self._running = False` を立てる
- しかし `http2_low.Connection.is_closed()` を確認する経路が無い
- 上記 3 経路のいずれも通らずに低レベルだけがクローズするケースが存在する。`src/bindings/http2.cpp` の `Http2Connection::receive` (`nghttp2_session_mem_recv` が負を返した分岐) と `Http2Connection::send` (`nghttp2_session_mem_send` が負を返した分岐) はどちらも `closed_ = true` にするだけでイベントを push しない。この状態では `Client.run` は `_receive` で TimeoutError → `_send_pending` は None → `next_event()` も None、というアイドルサイクルを永久に回し続ける
- サーバー側 `src/webtransport/http2/server.py` の `_handle_client` は `if connection.is_closed(): break` で同種のチェックを実装している。クライアント側だけがこの対称性を欠いている

## 設計方針

- `Client.run` のメインループで `self._connection.is_closed()` を確認し、True なら `_running = False` にする
- チェックの位置は `while True: next_event()` のイベント処理ループの直後、`await asyncio.sleep(0.01)` の前とする (低レベルが吐き出す最後のイベント (`GO_AWAY` 受信時に push される Http2EventType::GoAway 等) を取りこぼさないため、イベント処理を通してから状態確認する)
- 既存の GO_AWAY イベント経路・TCP EOF 経路・`close()` の同期経路はそのまま残す。`GO_AWAY` 受信時は `src/bindings/http2.cpp` の `on_frame_recv_callback` が Http2EventType::GoAway を push した直後に `closed_ = true` を立てるため、GO_AWAY イベント経路と `is_closed()` チェック経路の両方が立つ。どちらの経路で `_running = False` になっても副作用が無いこと (二重終了処理でエラーにならないこと) を実装で担保する
- エラーコード・エラーメッセージをアプリへ通知する API の追加は本 issue の対象外 (必要になれば別途 `add` カテゴリの issue を起票する。今回は「ハングを止める」ことだけをスコープとする)

## 完了条件

- `http2_low.Connection` に不正フレーム (`Http2Connection::receive` が負値を返す入力) を渡した後、`is_closed() == True` かつ次イベント無しの状態で、進行中の `Client.run()` がそれ以降のイテレーションで終了する。この経路は既存の GO_AWAY イベント経路・TCP EOF 経路・`close()` の同期経路のいずれとも独立に発火するため、`is_closed()` チェック追加の効果を単独で検証できる
- 既存の GO_AWAY 受信ケースおよび `close()` 呼び出しケースが `is_closed()` チェック追加後も引き続き終了することを回帰テストで確認する (回帰確認としては意味を持つが、is_closed() チェック単独の効果検証にはならない点を承知の上で残す)
- 上記を検証するテストを追加する。`AGENTS.md` の「モックやスタブは絶対に利用しないこと」に従い、フレームエラー再現は `http2_low.Connection` を直接叩いて意図的に壊れたバイト列 (例: HTTP/2 プリフェイスの後に不正な frame type を含むバイト列) を `receive()` に渡すテストで実現する。既存 GO_AWAY / `close()` 経路の回帰は実際の `webtransport.http2.Server` と `webtransport.http2.Client` を組み合わせた e2e テストで検証する
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する (`AGENTS.md`「テストはコメントを重視すること」)

## 解決方法

- 変更対象: `src/webtransport/http2/client.py`
  - `Client.run` のメインループのイベント処理ループ (`while True: next_event()`) の直後、`await asyncio.sleep(0.01)` の前で `if self._connection.is_closed(): self._running = False` を追加する
- 変更対象: テストファイル
  - HTTP/2 テストの既存配置に合わせて、次の 3 ケースを追加する:
    - フレームエラー経路: `http2_low.Connection` に不正バイト列を流し、`is_closed() == True` かつ次イベント無しになった状態で `Client.run()` が終了することを検証する
    - GO_AWAY 受信経路 (回帰): サーバーが `goaway()` を呼び、クライアントが GO_AWAY を受信して `Client.run()` が終了することを検証する
    - `close()` 呼び出し経路 (回帰): クライアント自身が `close()` を呼び、`Client.run()` が終了することを検証する
- 変更対象外: `src/webtransport/http2/client.py` の `_receive` / `_send_pending` / `close` (現行挙動を維持する。`_send_pending` は `_receive` の後に呼ばれるため、順序を変えずに追加するだけで足りる)
- 変更対象外: `src/webtransport/http3/client.py` (0107 のスコープ)
