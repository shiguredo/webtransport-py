# HTTP/3 のプロトコルエラーで run() が無限ハングする問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-error-path-silent
- Polished: 2026-08-21

## 目的

HTTP/3 の低レベル `Http3Connection::receive_stream_data` / `Http3Connection::get_streams_to_send` が nghttp3 のエラー (`nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` の負値 return) を握りつぶすため、`closed_` も立たず高レベル `webtransport.http3.Client.run()` / `Server.run()` が終了せず、QUIC 側の `idle_timeout_ns` (`Client` の既定 30 秒) までハングし続ける。この bug を修正し、bindings 側で `closed_ = true` を立てて、高レベル層で `is_closed()` を検知して QUIC CONNECTION_CLOSE を送出しつつ run() を終了する。

エラーコード・エラーメッセージをアプリへ通知する API の追加 (新 `Http3EventType::Error` バリアント・`Http3Event.error_message` フィールド追加・`on_connection_error` コールバック等) は本 issue の対象外とし、フォローアップの別 `add` issue で扱う (先例 0113 と同じ切り分け方針)。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` は `nghttp3_conn_read_stream2` が負値を返しても `return 0` するだけで、`closed_` を立てずイベントも push しない
- `src/bindings/http3.cpp` の `Http3Connection::get_streams_to_send` は `nghttp3_conn_writev_stream` が負値を返しても `break` するだけで `closed_` を立てない
- HTTP/2 側 `src/bindings/http2.cpp` の `Http2Connection::receive` / `send` は `nghttp2_session_mem_recv` / `mem_send` が負値を返した場合に `closed_ = true` を立てる。HTTP/3 側だけがこの対称性を欠く
- 隣接する `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` は既に `nghttp3_conn_read_stream2` の負値を検知して `H3EventType::Error` イベントを push しているが、`closed_ = true` は立てていない (Error 通知パターンはあるが closed_ 昇格は未対応)。同ファイルの `H3Session::get_streams_to_send` も `writev_stream` 負値時に `break` のみで `closed_` も Error push もしない。本 issue では http3.cpp 側の `closed_ = true` 対応だけを扱い、webtransport_h3.cpp 側の同種バグと、通知 API 全般の統合設計は別の add / fix issue で扱う
- `src/webtransport/http3/client.py` の `Client.run` メインループは `self._http3_connection.is_closed()` を確認する経路が無い。QUIC の `quic_low.EventType.CONNECTION_CLOSED` イベントだけを見て `_running = False` を立てているため、HTTP/3 層でのプロトコルエラー時は QUIC アイドルタイムアウト (`_idle_timeout_ns` 既定 30 秒) までハング
- `src/webtransport/http3/server.py` の `Server.run` メインループも同様に `client.http3_connection.is_closed()` を確認する経路が無い。`Server.run` は per-server の 1 本ループで `self._clients[addr]` の辞書ベースにクライアント状態を管理する構造 (`Client.run` のような per-connection ループは無い)。加えて `Server.run` は `sock_recvfrom` を `asyncio.wait_for(..., timeout=0.1)` で回すため、closed 後にピアが黙り込むと以降は TimeoutError 分岐にだけ入り、受信成功後の分岐にある is_closed() チェックが発火しないケースが生じ得る
- 先例 0113 (closed) は HTTP/2 版で `Client.run` にサーバー側 `_handle_client` と対称の `if self._connection.is_closed(): self._running = False` チェックを追加した。本 issue は HTTP/3 版でこの is_closed() 検知パターンを踏襲しつつ、加えて RFC 9114 Section 5.3 の Immediate Application Closure に沿って QUIC 層の `CONNECTION_CLOSE` も明示送出する (0113 は HTTP/2 で GOAWAY を追加送出しない設計だったのに対し、本 issue は HTTP/3 で CONNECTION_CLOSE を追加送出する点が差分)

## 設計方針

- `src/bindings/http3.cpp`:
  - `Http3Connection::receive_stream_data` の `nghttp3_conn_read_stream2` が負値を返した分岐で `closed_ = true` を立てる (HTTP/2 の `mem_recv` 負値時と対称)
  - `Http3Connection::get_streams_to_send` の `nghttp3_conn_writev_stream` が負値を返した分岐で `closed_ = true` を立てる (HTTP/2 の `mem_send` 負値時と対称)
  - どちらもイベント push はしない (通知 API は別 add issue のスコープ)
- QUIC 層に送出する error_code は `H3_GENERAL_PROTOCOL_ERROR = 0x0101` を暫定既定として使用する。RFC 9114 Section 8.1 が「Peer violated protocol requirements in a way that does not match a more specific error code」と定義し、nghttp3 が負値 (概ねピアのプロトコル違反に起因) を返した状況と意味が整合する。`H3_NO_ERROR (0x0100)` は「there is no error to signal」の定義と不整合なため使用しない。詳細な nghttp3 内部エラーコード (`NGHTTP3_ERR_H3_FRAME_ERROR` 等) から H3 ワイヤーコード (`H3_FRAME_ERROR`, `H3_FRAME_UNEXPECTED`, `H3_MESSAGE_ERROR` 等) への具体マッピングは別 add issue のスコープとする
- `H3_GENERAL_PROTOCOL_ERROR` constant の配置場所: 循環 import を避けるため `src/webtransport/http3/constants.py` を新設して 1 箇所定義し、`client.py` / `server.py` / `__init__.py` の 3 者から import する (現行 `__init__.py` は `from webtransport.http3.client import Client` を top-level で行っているため、`client.py` から `from webtransport.http3 import ...` を書くと Python の import 解決が `webtransport.http3` の部分初期化状態を掴んで ImportError を起こす。これを回避するため、Client / Server import に依存しない独立ファイルに定数を置く)
- `src/webtransport/http3/client.py` の `Client.run`:
  - メインループの HTTP/3 イベント処理ループ (`while True: http3_event = self._http3_connection.next_event()`) の直後、`await self._send_pending()` を通してから、`await asyncio.sleep(0.01)` の前で `self._http3_connection.is_closed()` を確認する
  - True なら `self._quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")` で QUIC CONNECTION_CLOSE を送出したうえで、`self._running = False` を立てる
  - CONNECTION_CLOSE 送出後は QUIC が draining 状態に入り新規データ生成が止まるため、`_send_pending` の 1 パケット制約 (`src/webtransport/http3/client.py` の「send() の連続 drain は ACK 待ちが必要なケースでハングする」制約) は close 後の drain には適用しない。close() 直後は残存パケットをすべて吐き切る drain-all を行う (専用のヘルパを新設するか、`_send_pending` に close 後専用のオプションを追加するかは実装判断)
- `src/webtransport/http3/server.py` の `Server.run`:
  - メインループの HTTP/3 イベント処理ループの直後、`await self._send_to(addr, client)` を通してから、対象 client の `client.http3_connection.is_closed()` を確認する
  - True なら `client.quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")` を呼び、`_send_to` (これも drain-all 化する) で CONNECTION_CLOSE を送出、既存 `CONNECTION_CLOSED` ハンドラと同じ in ガード `if addr in self._clients: del self._clients[addr]` を使って辞書から削除する (Server 全体は停止しない)
  - `Server.run` の TimeoutError 分岐 (`sock_recvfrom` が 0.1 秒でタイムアウトした場合) にも is_closed() 検知が必要。既存の per-client タイマー処理ループ (現状で `client.quic_connection.get_timeout()` を回している箇所) と同じ層に、per-client の `if client.http3_connection is not None and client.http3_connection.is_closed(): ...` チェックを追加し、closed になったクライアントを毎周回ずつ回収する
- 既存の QUIC `CONNECTION_CLOSED` イベント経路は現状のまま残す。`is_closed()` チェックと重複しても副作用は無い (bool への `False` 代入は idempotent、`del self._clients[addr]` は in ガードで二重削除を回避)
- `_quic_connection.close(uint64_t error_code, const std::string& reason)` は `src/webtransport/quic.pyi` (`Connection.close(error_code: int = 0, reason: str = '')`) および `src/bindings/quic.cpp` に既存 API として存在する (追加実装不要)

## 完了条件

- `src/bindings/http3.cpp` の `receive_stream_data` / `get_streams_to_send` が nghttp3 の負値 return 時に `closed_ = true` を立てるようになっている (現状は `return 0` / `break` のみ)
- `src/webtransport/http3/client.py` の `Client.run` に `is_closed()` チェックが追加され、0113 の HTTP/2 版で使用した is_closed() 検知パターンを踏襲したうえで、追加で QUIC 層の `close(H3_GENERAL_PROTOCOL_ERROR, ...)` を呼んで CONNECTION_CLOSE を明示送出する (0113 との差分)
- `src/webtransport/http3/server.py` の `Server.run` に per-client の is_closed() チェックが 2 箇所 (受信成功後の分岐 + タイムアウト分岐と同じ層の per-client タイマー処理) に追加され、closed になったクライアントは in ガード付きで `self._clients` から削除される
- `is_closed()` チェック発火時に QUIC `close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")` が呼ばれ、CONNECTION_CLOSE が draining 前に確実にピアへ送出される (RFC 9114 Section 5.3、close 後 drain-all で 1 パケット制約を回避)
- `H3_GENERAL_PROTOCOL_ERROR = 0x0101` が `src/webtransport/http3/constants.py` (新設) に 1 箇所定義され、`client.py` / `server.py` は `from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR` で直接 import して循環 import を回避、`__init__.py` は `constants` から再エクスポートして公開する
- fix の効果を直接検証する e2e テストが追加されている: 実 `Client` と `Server` を接続後、`Client._quic_connection.send_stream_data` (または `Server` 側の同等 API) を使ってピアのコントロールストリームまたは request stream に不正な HTTP/3 フレーム (不正な frame type / 不正な frame length / prohibited フィールド重複等、`nghttp3_conn_read_stream2` が負値 `NGHTTP3_ERR_H3_FRAME_ERROR` 等を返すバイト列) を注入し、受信側の `http3_connection.is_closed()` が True になり、対応する `run()` がハングせず数秒以内に終了することを検証する。注入する不正バイト列の具体形は実装時に nghttp3 の挙動を実験して確定する (RFC 9114 Section 5.5 で silently discard される未知フレーム type は不可、`H3_FRAME_ERROR` を確実に発生させるパターンを選ぶこと)。誘発できるパターンが実験の結果見つからなければ、bindings 側にテスト専用ヘルパを追加する別 issue (先例 0129) を起票してから本 issue の完了とする
- 既存の QUIC `CONNECTION_CLOSED` 経路が `is_closed()` チェック追加後も引き続き `Client.run()` / `Server.run` の該当 client を回収することを回帰テストで確認する
- `AGENTS.md`「モックやスタブは絶対に利用しないこと」に従い、実際の `webtransport.http3.Server` と `webtransport.http3.Client` を組み合わせた e2e として書く
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する
- 既存 e2e テスト (`tests/test_e2e_http3.py` 等) がすべて pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` を修正:
  - `nghttp3_conn_read_stream2` の負値分岐に `closed_ = true;` を追加
- `src/bindings/http3.cpp` の `Http3Connection::get_streams_to_send` を修正:
  - `nghttp3_conn_writev_stream` の負値分岐に `closed_ = true;` を追加
- `src/webtransport/http3/constants.py` を新設:
  - `H3_GENERAL_PROTOCOL_ERROR = 0x0101` を module 定数として定義 (RFC 9114 Section 8.1)
  - 依存モジュール無し (Client / Server を import しない)
- `src/webtransport/http3/__init__.py`:
  - `from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR` を追加して公開 API に含める (`__all__` に追加)
- `src/webtransport/http3/client.py` の `Client.run` メインループに以下を追加:
  - `from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR` (`__init__.py` 経由ではなく直接 `constants.py` を指すことで循環 import を回避)
  - HTTP/3 イベント処理ループ (`while True: http3_event = self._http3_connection.next_event()`) と `await self._send_pending()` を通した直後、`await asyncio.sleep(0.01)` の前で `if self._http3_connection.is_closed(): ...` を評価
  - True なら `self._quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")` を呼び、close 後の drain-all で残存パケット (CONNECTION_CLOSE 含む) をすべて吐き切り、`self._running = False` を立てる
- `src/webtransport/http3/server.py` の `Server.run` メインループに以下を追加:
  - `from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR`
  - HTTP/3 イベント処理ループと `await self._send_to(addr, client)` を通した直後、`client.http3_connection.is_closed()` を評価
  - True なら `client.quic_connection.close(H3_GENERAL_PROTOCOL_ERROR, "http3 protocol error")` を呼び、`_send_to` (close 後は drain-all 化) で送出、既存 CONNECTION_CLOSED ハンドラと対称の `if addr in self._clients: del self._clients[addr]` で回収する
  - タイムアウト分岐と同じ層の per-client タイマー処理ループにも、per-client の is_closed() チェックを追加する
- `tests/test_e2e_http3.py` に次の 4 ケースを追加:
  - `test_http3_client_run_exits_on_frame_error`: `Client` から不正 HTTP/3 フレームバイト列を送出し、対向 `Server` 側の `http3_connection.is_closed()` が True になり、対向 `Client` 側にも CONNECTION_CLOSE が届いて `Client.run()` が終了することを検証 (fix の直接検証、受信成功後分岐)
  - `test_http3_server_removes_client_on_timeout_path_after_close`: Server が不正フレームで対向 client を閉じたあと、当該 client からのパケット送信が止まった状態で `sock_recvfrom` が TimeoutError 分岐に落ちる状況を作り、per-client タイマー処理層の is_closed() チェック経路で client が `self._clients` から回収されることを検証 (Server.run TimeoutError 分岐カバレッジの直接検証)
  - `test_http3_client_run_exits_on_quic_connection_closed`: QUIC `CONNECTION_CLOSED` 経路が `is_closed()` チェック追加後も引き続き `Client.run()` を終了させる回帰
  - `test_http3_server_run_removes_client_on_quic_connection_closed`: QUIC `CONNECTION_CLOSED` を受けた `Server.run` が該当 client を `self._clients` から削除する回帰
- 変更対象: `src/bindings/http3.cpp` / `src/webtransport/http3/constants.py` (新設) / `src/webtransport/http3/__init__.py` / `src/webtransport/http3/client.py` / `src/webtransport/http3/server.py` / `tests/test_e2e_http3.py`
- 変更対象外: `src/bindings/webtransport_h3.cpp` (WebTransport 経路の同種バグは 0131 (open) で扱う)
- 変更対象外: `src/webtransport/http2/*` (HTTP/2 版は 0113 で完了)
- 変更対象外: `Http3EventType::Error` 相当の追加とアプリへのエラーコード通知 API (0130 (open) で扱う)
- 変更対象外: nghttp3 内部エラーコード (`NGHTTP3_ERR_*`) から H3 ワイヤーコード (`H3_FRAME_ERROR` 等) への詳細マッピング (別 add issue のスコープ)
