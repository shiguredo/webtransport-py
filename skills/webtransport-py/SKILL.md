---
name: webtransport-py
description: Python の WebTransport ライブラリ webtransport-py (import 名 webtransport) の利用リファレンス。WebTransport over HTTP/3 / HTTP/2、QUIC、HTTP/3、HTTP/2 の asyncio API と Sans I/O API、Config、イベント処理、0-RTT、Connection Migration、証明書検証、DATAGRAM の使い方に関する質問で使用。
---

# webtransport-py

Sans I/O アーキテクチャを採用した WebTransport の Python ライブラリ。WebTransport over HTTP/3 と WebTransport over HTTP/2 の両方に対応し、QUIC、HTTP/3、HTTP/2 を単体のプロトコルとしても利用できる。

## 概要

- Sans I/O アーキテクチャ
  - プロトコル処理と I/O を分離し、任意のイベントループやフレームワークと統合できる
  - QUIC は ngtcp2、HTTP/3 は nghttp3、HTTP/2 は nghttp2 を採用
- 二層 API 設計
  - Sans I/O API: プロトコル処理のみを提供する低レベル API (C 拡張)
  - asyncio API: すぐに使える高レベルなクライアント / サーバー実装
- Python Free-Threading (3.14t) 対応

## インストールと動作環境

```bash
uv add webtransport-py
```

- 配布名は `webtransport-py`、import 名は `webtransport`
- 実行時依存はゼロ
- Python 3.14 / 3.14t
- Ubuntu 24.04 LTS x86_64 / arm64、macOS 26 arm64

## モジュール構成

トップレベル `webtransport` パッケージはサブモジュール 5 つのみを公開する。

| モジュール | 提供内容 | Sans I/O の主クラス | asyncio API | トランスポート |
|---|---|---|---|---|
| `webtransport.h3` | WebTransport over HTTP/3 | `Session` | `Server` / `Client` | UDP + QUIC |
| `webtransport.h2` | WebTransport over HTTP/2 (RFC 9297 Capsule) | `Session` | `Server` / `Client` / `SessionWriter` | TCP + TLS |
| `webtransport.quic` | QUIC 単体 | `Connection` | `Server` / `Client` | UDP |
| `webtransport.http3` | HTTP/3 単体 | `Connection` | `Server` / `Client` | UDP + QUIC |
| `webtransport.http2` | HTTP/2 単体 | `Connection` | `Server` / `Client` | TCP + TLS |

`quic` / `http3` / `http2` には `get_version()` があり、それぞれ ngtcp2 / nghttp3 / nghttp2 のバージョン文字列を返す。

## asyncio API

いずれのモジュールも次の共通パターンを持つ。

- サーバー: コンストラクタ → `on_*()` でコールバック登録 → `async with server:` → `await server.run()`
- クライアント: コンストラクタ → `on_*()` でコールバック登録 → `await client.connect()` (成功で `True`) → 送信 → `await client.run()` → `await client.close()`
- コールバックはすべて async 関数を渡す
- `run()` は受信ループなので、クライアントでは `asyncio.wait_for(client.run(), timeout=...)` や `asyncio.create_task()` と組み合わせる

### WebTransport over HTTP/3 (`webtransport.h3`)

サーバー:

```python
import asyncio

from webtransport import h3


async def main() -> None:
    server = h3.Server(
        host="0.0.0.0",
        port=4433,
        certfile="cert.pem",
        keyfile="key.pem",
    )
    # コールバックのシグネチャ (すべて async)
    # on_session_ready(session_id: int, addr: tuple[str, int])
    # on_session_closed(session_id: int, addr: tuple[str, int])
    # on_stream_data(session_id: int, stream_id: int, data: bytes, addr: tuple[str, int])
    # on_stream_reset(session_id: int, stream_id: int, error_code: int, addr: tuple[str, int])
    # on_datagram(session_id: int, data: bytes, addr: tuple[str, int])

    async def on_stream_data(
        session_id: int, stream_id: int, data: bytes, addr: tuple[str, int]
    ) -> None:
        # エコーバック
        await server.send_stream_data(addr, stream_id, data)

    async def on_datagram(session_id: int, data: bytes, addr: tuple[str, int]) -> None:
        await server.send_datagram(addr, session_id, data)

    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    async with server:
        print(f"サーバー開始: {server.host}:{server.actual_port}")
        await server.run()


asyncio.run(main())
```

`h3.Server.__init__(host, port, certfile=None, keyfile=None, idle_timeout_ns=30_000_000_000)`。セッションはサーバーが自動で accept する。主なメソッド:

```python
async def send_stream_data(addr: tuple[str, int], stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(addr: tuple[str, int], session_id: int, data: bytes) -> None
async def reset_stream(addr: tuple[str, int], stream_id: int, error_code: int = 0) -> None
```

クライアント:

```python
import asyncio

from webtransport import h3


async def main() -> None:
    client = h3.Client(
        url="https://localhost:4433/webtransport",
        verify_peer=False,
    )
    # コールバックのシグネチャ (すべて async、サーバーと違い addr は付かない)
    # on_session_ready(session_id: int) / on_session_closed(session_id: int)
    # on_stream_data(stream_id: int, data: bytes)
    # on_stream_reset(stream_id: int, error_code: int)
    # on_datagram(data: bytes)

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        print(f"データ受信: {data}")

    client.on_stream_data(on_stream_data)

    if not await client.connect():
        print("接続失敗")
        return

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"Hello via stream!")
    await client.send_datagram(b"Hello via datagram!")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()


asyncio.run(main())
```

`h3.Client.__init__(url, verify_peer=True, idle_timeout_ns=30_000_000_000, ca_file=None, verify_callback=None)`。`url` は `https://host:port/path` 形式 (ポート省略時 443)。主なメソッド:

```python
async def connect() -> bool
async def open_stream(unidirectional: bool = False) -> int
async def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(data: bytes) -> None
async def reset_stream(stream_id: int, error_code: int = 0) -> None
```

### WebTransport over HTTP/2 (`webtransport.h2`)

`h2.Server.__init__(host, port, certfile, keyfile)` (証明書は必須)。サーバーのコールバックは末尾に `SessionWriter` を受け取る点が h3 と異なる。

```python
# on_session_ready(session_writer: SessionWriter)
# on_session_closed(session_writer: SessionWriter)
# on_stream_data(stream_id: int, data: bytes, session_writer: SessionWriter)
# on_stream_reset(stream_id: int, error_code: int, session_writer: SessionWriter)
# on_datagram(data: bytes, session_writer: SessionWriter)
```

`SessionWriter` はセッション単位の送信ハンドルで、`session_id` プロパティと次のメソッドを持つ。

```python
async def open_stream(unidirectional: bool = False) -> int
async def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(data: bytes) -> None
async def reset_stream(stream_id: int, error_code: int = 0) -> None
async def close_session(error_code: int = 0, error_message: str = "") -> None
```

`h2.Client.__init__(url, verify_peer=True, origin="")`。メソッドとコールバックの形は `h3.Client` と同じ。`connect()` は対向の SETTINGS を待ってから Extended CONNECT を送る。

### QUIC (`webtransport.quic`)

`quic.Server.__init__(host, port, certfile=None, keyfile=None, alpn_protocols=None, idle_timeout_ns=30_000_000_000)`。`alpn_protocols` の既定は `["h3"]`。

```python
# サーバーコールバック
# on_handshake_completed(addr: tuple[str, int])
# on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int])
# on_datagram(data: bytes, addr: tuple[str, int])
# on_connection_closed(addr: tuple[str, int])

async def open_stream(addr: tuple[str, int], bidirectional: bool = True) -> int
async def send_stream_data(addr: tuple[str, int], stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(addr: tuple[str, int], data: bytes) -> None
```

`quic.Client`:

```python
def __init__(
    self,
    host: str,
    port: int,
    alpn_protocols: list[str] | None = None,  # 既定 ["h3"]
    idle_timeout_ns: int = 30_000_000_000,
    verify_peer: bool = True,
    ca_file: str | None = None,
    verify_callback: Callable[[list[bytes]], bool] | None = None,
    session_ticket: bytes | None = None,
    early_transport_params: bytes | None = None,
    enable_early_data: bool = True,
) -> None
```

```python
# クライアントコールバック (addr なし)
# on_handshake_completed() / on_connection_closed()
# on_stream_data(stream_id: int, data: bytes, fin: bool)
# on_datagram(data: bytes)
# on_session_ticket(ticket: bytes)

async def connect() -> bool
async def open_stream(bidirectional: bool = True) -> int
async def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(data: bytes) -> None
async def migrate() -> bool  # Connection Migration
def export_session_ticket() -> bytes
def export_0rtt_transport_params() -> bytes
def is_early_data_accepted() -> bool
def was_early_data_attempted() -> bool
```

0-RTT / Session Resumption は「初回接続で `on_session_ticket` (または `export_session_ticket()` / `export_0rtt_transport_params()`) を保存 → 再接続時に `session_ticket` と `early_transport_params` をコンストラクタへ渡す」という流れで使う。証明書のカスタム検証は `verify_callback` に DER 形式の証明書チェーン (`list[bytes]`) を受け取って `bool` を返す関数を渡す。

### HTTP/3 (`webtransport.http3`)

`http3.Server.__init__(host, port, certfile=None, keyfile=None, idle_timeout_ns=30_000_000_000)`。

```python
# サーバーコールバック
# on_request(stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int])
# on_data(stream_id: int, data: bytes, addr: tuple[str, int])
# on_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int])

async def submit_response(addr: tuple[str, int], stream_id: int, headers: list[tuple[str, str]]) -> None
async def send_data(addr: tuple[str, int], stream_id: int, data: bytes, fin: bool = False) -> None
```

`http3.Client.__init__(host, port=443, idle_timeout_ns=30_000_000_000, verify_peer=True, ca_file=None, verify_callback=None)`。

```python
# クライアントコールバック
# on_headers(stream_id: int, headers: list[tuple[str, str]])
# on_data(stream_id: int, data: bytes)
# on_stream_end(stream_id: int)
# on_stream_reset(stream_id: int, error_code: int)

async def request(method: str, path: str, headers: list[tuple[str, str]] | None = None) -> int
async def send_data(stream_id: int, data: bytes, fin: bool = False) -> None
```

`request()` は `:method` `:path` `:scheme` `:authority` の擬似ヘッダーを自動で付与する。

### HTTP/2 (`webtransport.http2`)

`http2.Server.__init__(host, port, certfile, keyfile)` (証明書は必須、ALPN は `h2` 固定)。サーバーのコールバックは `ResponseWriter` を受け取る。

```python
# on_request(stream_id: int, headers: list[tuple[str, str]], response_writer: ResponseWriter)
# on_data(stream_id: int, data: bytes, response_writer: ResponseWriter)

# ResponseWriter のメソッド
async def send_headers(stream_id: int, headers: list[tuple[str, str]]) -> None
async def send_data(stream_id: int, data: bytes, end_stream: bool = False) -> None
```

`http2.Client.__init__(host, port=443, verify_peer=True)`。コールバックは `on_headers` / `on_data` / `on_stream_end`。`request()` の形は http3 と同じで、`send_data(stream_id, data, eof=False)` のみ引数名が異なる。

## Sans I/O API

I/O を一切行わず、「受信バイト列を入れる → イベントを取り出す → 送信バイト列を取り出す」の 3 段で回す。asyncio 以外のイベントループ (スレッド、trio、独自ループなど) と組み合わせるときに使う。

### QUIC (`quic.Connection`)

```python
# 生成
Connection.create_client(config: Config, local_addr: tuple[str, int], remote_addr: tuple[str, int]) -> Connection
Connection.create_server(config: Config) -> Connection
Connection.accept(config: Config, initial_packet: bytes, local_addr: tuple[str, int], remote_addr: tuple[str, int]) -> Connection

# 入出力
def receive(data: bytes, local_addr: tuple[str, int], remote_addr: tuple[str, int]) -> int
def send() -> Packet | None  # 1 回の呼び出しで 1 パケット
def next_event() -> Event | None

# タイマー (QUIC のみに存在する)
def get_timeout() -> int | None  # 次のタイムアウトまでのナノ秒
def handle_timeout() -> None

# 操作
def open_stream(bidirectional: bool = True) -> int
def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
def send_datagram(data: bytes) -> None
def reset_stream(stream_id: int, error_code: int = 0) -> None
def stop_sending(stream_id: int, error_code: int = 0) -> None
def close_stream(stream_id: int, error_code: int = 0) -> None  # RESET_STREAM + STOP_SENDING
def close(error_code: int = 0, reason: str = "") -> None
def initiate_migration(local_addr: tuple[str, int], remote_addr: tuple[str, int]) -> bool

# 0-RTT / 状態
def export_session_ticket() -> bytes
def export_0rtt_transport_params() -> bytes
def is_early_data_accepted() -> bool
def was_early_data_attempted() -> bool
def is_established() -> bool
def is_closed() -> bool
```

`send()` の戻り値 `Packet` は `data` / `local_host` / `local_port` / `remote_host` / `remote_port` を持つ。基本ループは次の形。

```python
# 受信したデータグラムを供給し、イベントを処理し、送信すべきパケットを吐き出す
connection.receive(udp_payload, local_addr, remote_addr)

while (event := connection.next_event()) is not None:
    if event.type == quic.EventType.STREAM_DATA:
        ...  # event.stream_id / event.data / event.fin

packet = connection.send()
if packet is not None:
    sock.sendto(packet.data, (packet.remote_host, packet.remote_port))

# タイマー処理 (イベントループ側で定期的に呼ぶ)
timeout = connection.get_timeout()
if timeout is not None and timeout <= 0:
    connection.handle_timeout()
```

`send()` は 1 回の呼び出しで 1 パケットだけ返す。`None` になるまでループで drain せず、受信・タイマーのたびに 1 回ずつ呼ぶこと (asyncio 実装がこのパターンを採っている)。

### WebTransport over HTTP/3 (`h3.Session`)

QUIC 層を持たないため、`quic.Connection` と結線して使う。

```python
Session.create_client(config: Config) -> Session
Session.create_server(config: Config) -> Session

# QUIC 層との入出力
def receive_stream_data(stream_id: int, data: bytes, fin: bool = False) -> int
def receive_datagram(data: bytes) -> None
def get_streams_to_send() -> list[tuple[int, bytes, bool]]  # (stream_id, data, fin)
def get_datagrams_to_send() -> list[bytes]
def get_required_streams() -> list[tuple[str, bool]]

# 制御ストリームの結び付け (QUIC 側で単方向ストリームを 3 本開いて割り当てる)
def bind_control_stream(stream_id: int) -> None
def bind_qpack_encoder_stream(stream_id: int) -> None
def bind_qpack_decoder_stream(stream_id: int) -> None

# セッション
def connect(stream_id: int, url: str) -> bool  # クライアント
def accept_session(stream_id: int) -> bool  # サーバー
def reject_session(stream_id: int, status_code: int) -> None
def close_session(session_id: int, error_code: int = 0, error_message: str = "") -> None

# ストリーム / データグラム
def open_stream(session_id: int, stream_id: int, is_unidirectional: bool) -> bool
def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
def send_datagram(session_id: int, data: bytes) -> None
def next_event() -> Event | None
```

結線パターン: `get_streams_to_send()` の結果を `quic.Connection.send_stream_data()` へ、`get_datagrams_to_send()` を `quic.Connection.send_datagram()` へ流す。逆方向は QUIC の `STREAM_DATA` / `DATAGRAM` イベントを `receive_stream_data()` / `receive_datagram()` へ渡す。

### HTTP/2 / WebTransport over HTTP/2 / HTTP/3

`http2.Connection` と `h2.Session` は TCP バイトストリーム型で、`receive(data: bytes) -> int` と `send() -> bytes | None`、`want_write() -> bool` を持つ。`h2.Session.connect(url, origin="") -> int` は session_id を返し、`is_webtransport_ready()` で対向の SETTINGS 受信を確認できる。`http3.Connection` は h3.Session と同様に QUIC 層と `receive_stream_data()` / `get_streams_to_send()` で結線する。

## Config の主要デフォルト値

Sans I/O API はモジュールごとの `Config` で設定する。主要なもの:

- `quic.Config`: `max_streams_bidi=100` / `max_streams_uni=100` / `max_data=1048576` / `idle_timeout_ns=30_000_000_000` / `verify_peer=False` / `enable_datagram=True` / `enable_early_data=True` / `alpn_protocols=[]` / `cert_file=""` / `key_file=""` / `ca_file=""` / `verify_callback=None`
- `http3.Config`: `max_field_section_size=65536` / `qpack_max_dtable_capacity=4096` / `qpack_blocked_streams=100` / `enable_webtransport=False` / `enable_h3_datagram=False` / `is_server=False`
- `h3.Config`: `max_field_section_size=65536` / `qpack_max_dtable_capacity=4096` / `qpack_blocked_streams=100` / `is_server=False`
- `http2.Config`: `initial_window_size=65535` / `max_concurrent_streams=100` / `max_frame_size=16384` / `max_header_list_size=65536` / `is_server=False`
- `h2.Config`: http2.Config の項目に加えて `wt_initial_max_data=1048576` / `wt_initial_max_stream_data=262144` / `wt_initial_max_streams_bidi=100` / `wt_initial_max_streams_uni=100`

## イベント型 (EventType)

`next_event()` が返す `Event` は `type` フィールドで分岐する。

- `quic.EventType`: `HANDSHAKE_COMPLETED` / `CONNECTION_CLOSED` / `STREAM_DATA` / `STREAM_OPENED` / `STREAM_CLOSED` / `STREAM_RESET` / `DATAGRAM` / `CONNECTION_ID_RETIRED` / `SESSION_TICKET` / `EARLY_DATA_REJECTED` / `PATH_VALIDATED` / `PATH_VALIDATION_FAILED`
- `http3.EventType`: `HEADERS` / `DATA` / `STREAM_END` / `PUSH_PROMISE` / `GO_AWAY` / `RESET` / `RESET_STREAM` / `STOP_SENDING` / `WEBTRANSPORT_SESSION_READY` / `WEBTRANSPORT_STREAM_DATA` / `WEBTRANSPORT_DATAGRAM`
- `h3.EventType`: `SESSION_READY` / `SESSION_CLOSED` / `STREAM_OPENED` / `STREAM_DATA` / `STREAM_CLOSED` / `RESET_STREAM` / `STOP_SENDING` / `DATAGRAM` / `ERROR`
- `http2.EventType`: `HEADERS` / `DATA` / `STREAM_END` / `STREAM_RESET` / `GO_AWAY` / `WINDOW_UPDATE` / `SETTINGS` / `PING`
- `h2.EventType`: `SESSION_READY` / `SESSION_CLOSED` / `SESSION_DRAINING` / `STREAM_DATA` / `STREAM_RESET` / `STOP_SENDING` / `DATAGRAM` / `ERROR`

`Event` の主なフィールド: `quic.Event` は `stream_id` / `data` / `fin` / `error_code` / `reason`、`h3.Event` / `h2.Event` は `session_id` / `stream_id` / `data` / `error_code` / `error_message`、`http3.Event` / `http2.Event` は `stream_id` / `headers` / `data` / `error_code`。

## 注意点

- `verify_peer` のデフォルトが層で異なる。asyncio の `Client` は `verify_peer=True`、Sans I/O の `quic.Config` は `verify_peer=False`。Sans I/O API を直接使うときは明示的に有効にすること
- `open_stream()` の引数名が層で異なる。`quic` は `bidirectional: bool = True`、`h3` / `h2` は `unidirectional: bool = False` (どちらもデフォルトは双方向)
- タイマー API (`get_timeout()` / `handle_timeout()`) があるのは `quic.Connection` のみ。`http3` / `h3` / `http2` / `h2` の Sans I/O クラスには無い
- 独自の例外クラスは定義されていない。生成系ファクトリの失敗は `RuntimeError`、asyncio ラッパーの未接続時操作も `RuntimeError` になる
- `webtransport.http2.ResponseWriter` はコールバック引数として渡されるが `http2/__init__.py` から再エクスポートされていない。型注釈で import する場合は `from webtransport.http2.server import ResponseWriter` を使う
- `h2.CapsuleType` (Capsule Protocol の型定数) も再エクスポートされていない。必要なら `from webtransport.webtransport_ext.h2 import CapsuleType` を使う

## サンプルコード

リポジトリの `examples/` に全モジュールの動くサンプルがある。

- `examples/webtransport/h3_server.py` / `h3_client.py`: WebTransport over HTTP/3 のエコーサーバーとクライアント
- `examples/webtransport/h2_server.py` / `h2_client.py`: WebTransport over HTTP/2 のエコーサーバーとクライアント
- `examples/quic/server.py` / `client.py`: QUIC 単体のエコーサーバーとクライアント
- `examples/http3/server.py` / `client.py`: HTTP/3 の最小サーバーと GET クライアント
- `examples/http2/server.py` / `client.py`: HTTP/2 の最小サーバーと GET クライアント
