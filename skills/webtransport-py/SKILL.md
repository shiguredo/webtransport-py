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
- クライアント: コンストラクタ → `on_*()` でコールバック登録 → `await client.connect()` (成功は例外なしの正常復帰、失敗は `WebTransportConnectError` 派生の具体例外) → 送信 → `await client.run()` → `await client.close()`
- コールバックはすべて async 関数を渡す
- `run()` は受信ループなので、クライアントでは `asyncio.wait_for(client.run(), timeout=...)` や `asyncio.create_task()` と組み合わせる。例外は `quic.Client` で、`connect()` がバックグラウンド受信タスクを起動するため `run()` は接続終了を待つだけの完了待ちになる (起動しなくても受信イベントは処理される。`asyncio.create_task(client.run())` で接続終了まで待てる)

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
        allowed_origins=["https://example.com"],
    )
    # コールバックのシグネチャ (すべて async)
    # on_session_ready(session_id: int, addr: tuple[str, int])
    # on_session_closed(session_id: int, addr: tuple[str, int])
    # on_stream_data(session_id: int, stream_id: int, data: bytes, addr: tuple[str, int])
    # on_stream_reset(session_id: int, stream_id: int, error_code: int, addr: tuple[str, int])
    #   on_stream_reset の session_id は WT ヘッダー未受信のままリセットされた
    #   ストリーム等では -1 になることがある
    # on_datagram(session_id: int, data: bytes, addr: tuple[str, int])
    #   on_datagram の session_id は Quarter Stream ID から復元する
    #   (draft-ietf-webtrans-http3-16 Section 4.5)。仕様逸脱ピアが巨大な
    #   Quarter Stream ID を送った場合は負の値になり得る

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        # サーバーから単方向ストリームを開いて送信する (デフォルト単方向)
        # 失敗時は -1 が返るため、送信前にガードする
        stream_id = await server.open_stream(addr, session_id)
        if stream_id >= 0:
            await server.send_stream_data(addr, stream_id, b"Hello from server!")

    async def on_stream_data(
        session_id: int, stream_id: int, data: bytes, addr: tuple[str, int]
    ) -> None:
        # エコーバック
        await server.send_stream_data(addr, stream_id, data)

    async def on_datagram(session_id: int, data: bytes, addr: tuple[str, int]) -> None:
        await server.send_datagram(addr, session_id, data)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    async with server:
        print(f"サーバー開始: {server.host}:{server.actual_port}")
        await server.run()


asyncio.run(main())
```

`h3.Server.__init__(host, port, certfile=None, keyfile=None, idle_timeout_ns=30_000_000_000, allowed_origins=None)`。`allowed_origins` は None / 空リストで全オリジンを受理する。セッションはサーバーが自動で accept する。プロパティは `host` / `port` / `actual_port` / `is_running`。主なメソッド:

```python
async def send_stream_data(addr: tuple[str, int], stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(addr: tuple[str, int], session_id: int, data: bytes) -> None
async def reset_stream(addr: tuple[str, int], stream_id: int, error_code: int = 0) -> None
async def close_stream(addr: tuple[str, int], stream_id: int, error_code: int = 0) -> None
async def open_stream(addr: tuple[str, int], session_id: int, unidirectional: bool = True) -> int
async def start() -> None
async def run() -> None
async def stop() -> None
```

`close_stream` は `reset_stream` に委譲する同一実装 (RESET_STREAM 送出)。`open_stream` はデフォルト単方向で、双方向指定 (`unidirectional=False`) は `NotImplementedError`、失敗時は -1 を返す。`session_id` には `on_session_ready` で受け取った有効な値を渡す (サーバー起動の双方向ストリームは draft-ietf-webtrans-http3-16 Section 4.3 の "can" に基づく任意実装のため未実装。双方向ストリーム自体はクライアントから開ける)。起動は `async with server:` でも `start()` / `stop()` の明示呼び出しでも行え、`run()` がメインループである。

送信系メソッドの `addr` は接続ごとのキーであり、コールバックで受け取った `addr` をそのまま渡す。キーに無い `addr` を渡した場合、エラーにならず処理が黙って捨てられる。

クライアント:

```python
import asyncio

from webtransport import WebTransportConnectError, h3


async def main() -> None:
    client = h3.Client(
        url="https://localhost:4433/webtransport",
        verify_peer=False,
        origin="https://example.com",
    )
    # コールバックのシグネチャ (すべて async、サーバーと違い addr は付かない)
    # on_session_ready(session_id: int) / on_session_closed(session_id: int)
    # on_stream_data(stream_id: int, data: bytes)
    # on_stream_reset(stream_id: int, error_code: int)
    # on_datagram(data: bytes)

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        print(f"データ受信: {data}")

    client.on_stream_data(on_stream_data)

    try:
        await client.connect()
    except WebTransportConnectError:
        print("接続失敗")
        await client.close()
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

`h3.Client.__init__(url, verify_peer=True, origin="", idle_timeout_ns=30_000_000_000, ca_file=None, verify_callback=None)`。`url` は `https://host:port/path` 形式 (ポート省略時 443)。`origin` は Origin ヘッダー値で、空文字なら付与しない。プロパティは `url` / `host` / `port` / `is_connected` / `session_id`。主なメソッド:

```python
async def connect(timeout: float = 10.0) -> None
async def open_stream(unidirectional: bool = False) -> int
async def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
async def send_datagram(data: bytes) -> None
async def reset_stream(stream_id: int, error_code: int = 0) -> None
async def close_stream(stream_id: int, error_code: int = 0) -> None
async def run() -> None
async def close() -> None
```

`close_stream` は `reset_stream` と同じ挙動 (RESET_STREAM 送出)。`open_stream` はデフォルト双方向で、失敗時は -1 を返す。失敗条件はセッション終了後・非 2xx 拒否後・未確立・接続クローズ済みに加え、Sans I/O の `h3.Session.open_stream` の登録失敗も含む (登録失敗時は開いた QUIC ストリームを RESET_STREAM で解放してから -1 を返す。サーバー側の `open_stream` と同じ)。`connect()` は deadline ベースで bounded に動作し、失敗時は `WebTransportConnectError` 派生の具体例外 (`ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError`) を送出する。`run()` が受信ループであり、`close()` でセッションと接続を閉じる。

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

`h2.Client.__init__(url, verify_peer=True, origin="")`。メソッドとコールバックの形は `h3.Client` と同じ (ただし `close_stream` は無く、リセットは `reset_stream` を使う)。`connect(timeout: float = 10.0) -> None` は対向の SETTINGS を待ってから Extended CONNECT を送る。失敗時は `WebTransportConnectError` 派生の具体例外 (`ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError`) を送出する。

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
    max_datagram_frame_size: int | None = None,  # None なら既定 65536、0 なら広告しない
) -> None
```

```python
# クライアントコールバック (addr なし)
# on_handshake_completed() / on_connection_closed()
# on_stream_data(stream_id: int, data: bytes, fin: bool)
# on_datagram(data: bytes)
# on_session_ticket(ticket: bytes)
# on_early_data_rejected()  (0-RTT early data が受け入れられなかったときに発火)

async def connect(timeout: float = 10.0) -> bool  # バックグラウンド受信タスクを起動し、ハンドシェイク完了を待つ (期限で False)
async def open_stream(bidirectional: bool = True) -> int
async def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
async def recv_stream_data(stream_id: int, timeout: float = 10.0, *, overall_timeout: float | None = None) -> tuple[bytes, bool]  # FIN まで受信し (データ, fin) を返す
async def shutdown_stream(stream_id: int, error_code: int = 0) -> None  # RESET_STREAM と STOP_SENDING を送出して中断
async def wait_for_stream_reset(stream_id: int, timeout: float = 10.0) -> int  # ピアの RESET_STREAM を待ちエラーコードを返す
async def send_datagram(data: bytes) -> None
async def migrate() -> bool  # Connection Migration
async def run() -> None  # バックグラウンド受信タスクの完了 (接続終了) まで待つ
def register_early_data(data: bytes, fin: bool = False) -> None  # 0-RTT として送信するデータを登録 (connect() の前のみ。登録ごとに双方向ストリームを 1 本開く)
def export_session_ticket() -> bytes
def export_0rtt_transport_params() -> bytes
def is_early_data_accepted() -> bool
def was_early_data_attempted() -> bool
```

0-RTT / Session Resumption は「初回接続で `on_session_ticket` (または `export_session_ticket()` / `export_0rtt_transport_params()`) を保存 → 再接続時に `session_ticket` と `early_transport_params` をコンストラクタへ渡す」という流れで使う。0-RTT で送るデータは `register_early_data()` で `connect()` の前に登録する (登録ごとに双方向ストリームを 1 本開いて送出する。0-RTT を試行しない接続ではストリームを開けないため送出されずに破棄され、警告ログが出る)。early data がピアに拒否された場合は `on_early_data_rejected()` が発火し、拒否されたデータと紐づくストリームの状態は破棄される (再送は呼び出し側でストリームを開き直す)。証明書のカスタム検証は `verify_callback` に DER 形式の証明書チェーン (`list[bytes]`) を受け取って `bool` を返す関数を渡す。

`connect()` はバックグラウンド受信タスクを起動し、ハンドシェイク完了を `timeout` (既定 10.0 秒) で打ち切る。期限までに確立できない場合は接続を維持したまま `False` を返す (ハンドシェイクが後で完了する可能性がある。後始末は `close()` が担う)。`timeout <= 0` では接続を開始せず即座に `False` を返す。タイムアウト後は `_recv_task` が存続するため、同じ `Client` で `connect()` を再呼び出しすると `RuntimeError` になる (再試行には新規 `Client` が必要)。`run()` はバックグラウンド受信タスクの完了 (接続終了) を待つだけの役割で、`asyncio.create_task(client.run())` で接続終了まで待つ用途に使う。`max_datagram_frame_size` は DATAGRAM の受信サポート広告 (RFC 9221 Section 3) で、0 なら広告しない (既定 65536)。0 を指定すると低レベル設定の `enable_datagram` が False になり、受信広告だけでなくローカルの `send_datagram()` も無効化される (RFC 9221 は単方向利用を認めるが、本実装では受信と送信が連動する)。

`recv_stream_data()` は呼び出し時点で FIN 完了済みなら即時 return する。タイムアウトは idle deadline (`timeout`) と absolute deadline (`overall_timeout`。None なら `max(timeout * 6, 30)`) の 2 段構えで、どちらかに達すると `TimeoutError` を raise する。接続終了 (CONNECTION_CLOSED) を受信した場合も `TimeoutError` を raise して待機を終了する。STREAM_RESET 受信時は進捗として idle deadline が 1 回延長され、その後は idle timeout になる。コールバック内からは呼べない (`RuntimeError`)。`on_stream_data` コールバックと併用してもデータは両方に配信される。受信データはストリームごとに保持され、`recv_stream_data` の対象外ストリームも保持される (FIN 完了済みの即時 return を実現するため)。

`shutdown_stream()` は低レベル `close_stream` を呼び、RESET_STREAM (RFC 9000 Section 19.4) と STOP_SENDING (Section 19.5) を送出する。双方向ストリームでは両方を送出し、単方向ストリームでは ngtcp2 がローカル単方向なら write 側 (RESET_STREAM) のみ、リモート単方向なら read 側 (STOP_SENDING) のみを shutdown する。`wait_for_stream_reset()` はピアの RESET_STREAM を待ち、そのアプリケーションエラーコードを返す。呼び出し時点で受信済みなら即時 return し、期限までに受信しない場合・接続終了時は `TimeoutError` を raise する。ngtcp2 は STOP_SENDING を受信すると、ストリームが Ready / Send 状態の場合は自動で RESET_STREAM を返す (RFC 9000 Section 3.5 の MUST。エラーコードは STOP_SENDING から複製する SHOULD。Data Sent 状態では MAY)。`shutdown_stream` はコールバック内から呼べる。`wait_for_stream_reset` はコールバック内からは呼べない (`RuntimeError`)。

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

# ストリーム / コネクション制御
streams_bidi_left -> int | None  # 開設可能な残り双方向ストリーム数
streams_uni_left -> int | None  # 開設可能な残り単方向ストリーム数
def keep_alive_timeout(timeout_ns: int) -> None  # keep-alive タイムアウト (UINT64_MAX で無効化)
def initiate_key_update() -> bool  # 鍵更新
def extend_max_offset(datalen: int) -> None  # コネクション全体のフロー制御拡張
def extend_max_stream_offset(stream_id: int, datalen: int) -> bool  # ストリームのフロー制御拡張
def extend_max_streams_bidi(n: int) -> None
def extend_max_streams_uni(n: int) -> None

# 0-RTT / 状態
def export_session_ticket() -> bytes
def export_0rtt_transport_params() -> bytes
def is_early_data_accepted() -> bool
def was_early_data_attempted() -> bool
def is_established() -> bool
def is_closed() -> bool
def is_handshake_completed() -> bool
def get_connection_id() -> bytes

# 接続統計 (プロパティ。取得前は None)
latest_rtt -> int | None  # 最新 RTT (ナノ秒)
min_rtt -> int | None
smoothed_rtt -> int | None
rttvar -> int | None
cwnd -> int | None  # 輻輳ウィンドウ (バイト)
ssthresh -> int | None
bytes_in_flight -> int | None  # 送信中で未 ACK のバイト数
pkt_sent -> int | None
bytes_sent -> int | None
pkt_recv -> int | None  # 破棄パケット除外
bytes_recv -> int | None
pkt_lost -> int | None  # PMTUD パケット除外
bytes_lost -> int | None
ping_recv -> int | None  # 受信 PING フレーム数
pkt_discarded -> int | None
pto -> int | None  # プローブタイムアウト
cwnd_left -> int | None  # 輻輳ウィンドウ残量
max_data_left -> int | None  # コネクション全体のフロー制御残量
def max_stream_data_left(stream_id: int) -> int | None
def stream_loss_count(stream_id: int) -> int | None
send_quantum -> int | None
path_max_tx_udp_payload_size -> int | None

# 接続状態・エラー・ピア情報 (プロパティ。取得前は None)
error_code -> int | None  # コネクションエラーコード
reason -> str | None  # コネクションエラーの理由
tls_error -> int  # ngtcp2 が記録した TLS 内部エラー (無ければ 0)
tls_alert -> int  # TLS アラート (無ければ 0)
remote_max_idle_timeout -> int | None
remote_max_udp_payload_size -> int | None
remote_initial_max_data -> int | None
remote_initial_max_stream_data_bidi_local -> int | None
remote_initial_max_stream_data_bidi_remote -> int | None
remote_initial_max_stream_data_uni -> int | None
remote_initial_max_streams_bidi -> int | None
remote_initial_max_streams_uni -> int | None
remote_max_datagram_frame_size -> int | None
local_max_idle_timeout -> int
local_max_udp_payload_size -> int
local_initial_max_data -> int
local_initial_max_stream_data_bidi_local -> int
local_initial_max_stream_data_bidi_remote -> int
local_initial_max_stream_data_uni -> int
local_initial_max_streams_bidi -> int
local_initial_max_streams_uni -> int
local_max_datagram_frame_size -> int
negotiated_version -> int  # ネゴシエーションされた QUIC バージョン (未確定なら 0)
client_chosen_version -> int
in_closing_period -> bool
in_draining_period -> bool
scid -> list[bytes]  # 送信元接続 ID 一覧
active_dcid -> list[bytes]  # アクティブな宛先接続 ID 一覧 (ハンドシェイク完了前は空)
```

`send()` の戻り値 `Packet` は `data` / `local_host` / `local_port` / `remote_host` / `remote_port` を持つ。基本ループは次の形。

```python
# 受信したデータグラムを供給し、イベントを処理し、送信すべきパケットを吐き出す
connection.receive(udp_payload, local_addr, remote_addr)

while (event := connection.next_event()) is not None:
    if event.type == quic.EventType.STREAM_DATA:
        ...  # event.stream_id / event.data / event.fin / event.offset

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

QUIC 層を持たないため、`quic.Connection` と結線して使う。公開メソッドは次のとおり。

```python
Session.create_client(config: Config) -> Session
Session.create_server(config: Config) -> Session

# QUIC 層との入出力
def receive_stream_data(stream_id: int, data: bytes, fin: bool = False) -> int
def receive_datagram(data: bytes) -> None
def get_streams_to_send() -> list[tuple[int, bytes, bool]]  # (stream_id, data, fin)
def get_datagrams_to_send() -> list[bytes]

# 制御ストリームの結び付け (QUIC 側で単方向ストリームを 3 本開いて割り当てる)
def get_required_streams() -> list[tuple[str, bool]]  # (名前, bidirectional)  # false = 単方向
def bind_control_stream(stream_id: int) -> None
def bind_qpack_encoder_stream(stream_id: int) -> None
def bind_qpack_decoder_stream(stream_id: int) -> None

# セッション
def connect(stream_id: int, url: str, origin: str = "") -> bool  # クライアント
def accept_session(stream_id: int) -> bool  # サーバー
def reject_session(stream_id: int, status_code: int) -> None
def close_session(session_id: int, error_code: int = 0, error_message: str = "") -> None
def is_closed() -> bool
def get_session_ids() -> list[int]
def get_session_streams(session_id: int) -> list[StreamInfo]

# ストリーム / データグラム
def open_stream(session_id: int, stream_id: int, is_unidirectional: bool) -> bool
def send_stream_data(stream_id: int, data: bytes, fin: bool = False) -> None
def send_datagram(session_id: int, data: bytes) -> None
def close_stream(stream_id: int, error_code: int = 0) -> int  # 戻り値はセッション ID (復元不可なら -1)
def reset_stream(stream_id: int, error_code: int = 0) -> None

# ストリーム状態 (None はコネクションが無いか閉じている場合。0 / 1 はストリームの状態)
def stream_writable(stream_id: int) -> int | None  # 1 書き込み可 / 0 書き込み不可
def stream_flushed(stream_id: int) -> int | None  # 1 受け渡し済み / 0 未了 (存在しないストリームは 1)
def stream_wt_session_id(stream_id: int) -> int | None  # ストリームが存在しない場合・WT データストリームでない場合も None

# フロー制御
def block_stream(stream_id: int) -> None
def unblock_stream(stream_id: int) -> bool
def max_concurrent_streams(n: int) -> None
def set_max_client_streams_bidi(max_streams: int) -> None  # サーバー (リクエストストリーム受け入れ前)

# イベント
def next_event() -> Event | None
```

`close_stream` は nghttp3 へのストリーム終了通知で、戻り値はリセットされたストリームが属するセッション ID (復元できない場合は -1)。`reset_stream` は `close_stream` を呼ぶだけであり、QUIC RESET_STREAM の送出は asyncio ラッパーの `reset_stream` が QUIC 層への通知と合わせて行う (Sans I/O で直接使う場合は `quic.Connection.reset_stream()` を自分で呼ぶ)。`connect` の `origin` は Origin ヘッダー値で、空文字なら付与しない。

`Config` のプロパティは `max_field_section_size` / `qpack_max_dtable_capacity` / `qpack_blocked_streams` / `is_server` / `allowed_origins`。`allowed_origins` は許可オリジンリストで、空リスト (未設定) なら全オリジンを受理する。

`Event` のフィールドは `type` / `session_id` / `stream_id` / `data` / `error_code` / `error_message` / `is_unidirectional`。`is_unidirectional` は値が設定される経路が無く常に False である (真偽の判定には使わないこと)。`StreamInfo` (セッションに属するストリーム情報) のフィールドは `stream_id` / `session_id` / `is_unidirectional` / `is_incoming` / `is_write_registered`。

結線パターン: `get_streams_to_send()` の結果を `quic.Connection.send_stream_data()` へ、`get_datagrams_to_send()` を `quic.Connection.send_datagram()` へ流す。逆方向は QUIC の `STREAM_DATA` / `DATAGRAM` イベントを `receive_stream_data()` / `receive_datagram()` へ渡す。

### HTTP/3 (`http3.Connection`)

`h3.Session` と同様に QUIC 層と `receive_stream_data()` / `get_streams_to_send()` で結線する。

```python
Connection.create_client(config: Config) -> Connection
Connection.create_server(config: Config) -> Connection

# QUIC 層との入出力
def receive_stream_data(stream_id: int, data: bytes, fin: bool = False) -> int
def get_streams_to_send() -> list[tuple[int, bytes, bool]]  # (stream_id, data, fin)
def get_required_streams() -> list[tuple[str, bool]]  # (名前, bidirectional)  # false = 単方向
def bind_control_stream(stream_id: int) -> None
def bind_qpack_encoder_stream(stream_id: int) -> None
def bind_qpack_decoder_stream(stream_id: int) -> None

# リクエスト / レスポンス
def submit_request(stream_id: int, headers: list[tuple[str, str]]) -> bool
def submit_response(stream_id: int, headers: list[tuple[str, str]]) -> bool
def send_data(stream_id: int, data: bytes, fin: bool = False) -> None
def reset_stream(stream_id: int, error_code: int = 0) -> None
def close_stream(stream_id: int, error_code: int = 0) -> None  # QUIC ストリーム終了を nghttp3 に通知
def goaway(id: int = 0) -> None

# 送信側拡張
def submit_trailers(stream_id: int, headers: list[tuple[str, str]]) -> bool  # トレーラ
def submit_info(stream_id: int, headers: list[tuple[str, str]]) -> bool  # 1xx レスポンス (サーバーのみ)
def submit_shutdown_notice() -> bool  # graceful shutdown の開始通知 (サーバーのみ)
def shutdown_stream_write(stream_id: int) -> None  # 書き込み側シャットダウン

# 優先度制御 (RFC 9218)
def stream_priority(stream_id: int) -> tuple[int, bool] | None  # ストリームの優先度 (サーバーのみ)
def client_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool
def server_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool

# ストリーム状態 / フロー制御
def stream_writable(stream_id: int) -> int | None  # 1 書き込み可 / 0 書き込み不可
def stream_flushed(stream_id: int) -> int | None  # 1 受け渡し済み / 0 未了
def frame_payload_left(stream_id: int) -> int | None  # 受信中フレームのペイロード残量
def block_stream(stream_id: int) -> None
def unblock_stream(stream_id: int) -> bool
def max_concurrent_streams(n: int) -> None
def set_max_client_streams_bidi(max_streams: int) -> None  # サーバー (リクエストストリーム受け入れ前)
def next_event() -> Event | None
```

モジュール関数 `parse_priority(value: str) -> tuple[int, bool] | None` は RFC 9218 の Priority ヘッダー値を (urgency, incremental) にパースする。

### HTTP/2 (`http2.Connection`)

`receive(data: bytes) -> int` / `send() -> bytes | None` / `want_write() -> bool` の TCP バイトストリーム型。

```python
Connection.create_client(config: Config) -> Connection
Connection.create_server(config: Config) -> Connection

def receive(data: bytes) -> int
def send() -> bytes | None
def want_write() -> bool  # 送信待ちデータがあるか

def submit_request(headers: list[tuple[str, str]]) -> int  # 返り値は stream_id
def submit_response(stream_id: int, headers: list[tuple[str, str]]) -> None
def send_data(stream_id: int, data: bytes, eof: bool = False) -> None
def reset_stream(stream_id: int, error_code: int = 0) -> None
def goaway(error_code: int = 0) -> None
def ping() -> None
def terminate_session(error_code: int = 0, last_stream_id: int = 0) -> bool  # GOAWAY を送って即時終了
def set_local_window_size(stream_id: int, window_size: int) -> bool  # ローカルウィンドウの動的変更
def submit_trailer(stream_id: int, headers: list[tuple[str, str]]) -> bool
def submit_priority_update(stream_id: int, urgency: int, incremental: bool) -> bool
def change_extpri_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool
def submit_push_promise(stream_id: int, headers: list[tuple[str, str]]) -> int  # Server Push (返り値は promised stream_id)
def next_event() -> Event | None

# セッション状態 (プロパティ)
remote_settings -> dict[str, int] | None  # ピアの SETTINGS
local_settings -> dict[str, int] | None
outbound_queue_size -> int | None  # 送信キューのフレーム数
remote_window_size -> int | None  # コネクションのリモートウィンドウ残量
local_window_size -> int | None
effective_recv_data_length -> int | None  # WINDOW_UPDATE 未送信の受信 DATA バイト数
request_allowed -> bool | None  # 新しいリクエストを送信できるか
def stream_remote_window_size(stream_id: int) -> int | None
def stream_local_window_size(stream_id: int) -> int | None
def stream_effective_recv_data_length(stream_id: int) -> int | None
def stream_local_close(stream_id: int) -> bool | None  # ローカル側が half-closed か
def stream_remote_close(stream_id: int) -> bool | None
```

モジュール関数 `get_version()` は nghttp2 のバージョンを、`select_alpn(protocols: list[str]) -> str | None` は h2 / http/1.1 の優先順で ALPN を選択する。

### WebTransport over HTTP/2 (`h2.Session`)

`http2.Connection` と同じ TCP バイトストリーム型。`Config` は http2.Config に加えて WebTransport 用の 4 項目を持つ。

```python
Session.create_client(config: Config) -> Session
Session.create_server(config: Config) -> Session

def receive(data: bytes) -> int
def send() -> bytes | None
def want_write() -> bool

def connect(url: str, origin: str = "") -> int  # クライアント。返り値は session_id
def is_webtransport_ready() -> bool  # 対向 SETTINGS で WebTransport over HTTP/2 が有効か
def accept_session(session_id: int) -> bool  # サーバー
def reject_session(session_id: int, status_code: int) -> None
def open_stream(session_id: int, is_unidirectional: bool) -> int
def send_stream_data(session_id: int, stream_id: int, data: bytes, fin: bool = False) -> None
def reset_stream(session_id: int, stream_id: int, error_code: int, reliable_size: int = 0) -> None
def stop_sending(session_id: int, stream_id: int, error_code: int) -> None
def send_datagram(session_id: int, data: bytes) -> None
def close_session(session_id: int, error_code: int = 0, error_message: str = "") -> None
def drain_session(session_id: int) -> None
def next_event() -> Event | None
def is_closed() -> bool
def get_session_ids() -> list[int]
def get_stream_ids(session_id: int) -> list[int]  # セッションに属するストリーム ID
```

## Config の主要デフォルト値

Sans I/O API はモジュールごとの `Config` で設定する。主要なもの:

- `quic.Config`: `max_streams_bidi=100` / `max_streams_uni=100` / `max_data=1048576` / `idle_timeout_ns=30_000_000_000` / `verify_peer=False` / `enable_datagram=True` / `max_datagram_frame_size=65536` / `enable_early_data=True` / `alpn_protocols=[]` / `server_name=""` / `cert_file=""` / `key_file=""` / `ca_file=""` / `verify_callback=None`
- `http3.Config`: `max_field_section_size=65536` / `qpack_max_dtable_capacity=4096` / `qpack_blocked_streams=100` / `enable_webtransport=False` / `enable_h3_datagram=False` / `is_server=False`
- `h3.Config`: `max_field_section_size=65536` / `qpack_max_dtable_capacity=4096` / `qpack_blocked_streams=100` / `is_server=False` / `allowed_origins=[]`
- `http2.Config`: `initial_window_size=65535` / `max_concurrent_streams=100` / `max_frame_size=16384` / `max_header_list_size=65536` / `is_server=False` / `send_preface=True` / `no_rfc7540_priorities=True`
- `h2.Config`: http2.Config の項目に加えて `wt_initial_max_data=1048576` / `wt_initial_max_stream_data=262144` / `wt_initial_max_streams_bidi=100` / `wt_initial_max_streams_uni=100`

## イベント型 (EventType)

`next_event()` が返す `Event` は `type` フィールドで分岐する。

- `quic.EventType`: `HANDSHAKE_COMPLETED` / `CONNECTION_CLOSED` / `STREAM_DATA` / `STREAM_OPENED` / `STREAM_CLOSED` / `STREAM_RESET` / `DATAGRAM` / `CONNECTION_ID_RETIRED` / `SESSION_TICKET` / `EARLY_DATA_REJECTED` / `PATH_VALIDATED` / `PATH_VALIDATION_FAILED`
- `http3.EventType`: `HEADERS` / `DATA` / `STREAM_END` / `PUSH_PROMISE` / `GO_AWAY` / `RESET` / `RESET_STREAM` / `STOP_SENDING` / `WEBTRANSPORT_SESSION_READY` / `WEBTRANSPORT_STREAM_DATA` / `WEBTRANSPORT_DATAGRAM`
- `h3.EventType`: `SESSION_READY` / `SESSION_CLOSED` / `STREAM_OPENED` / `STREAM_DATA` / `STREAM_CLOSED` / `RESET_STREAM` / `STOP_SENDING` / `DATAGRAM` / `ERROR`
- `http2.EventType`: `HEADERS` / `DATA` / `STREAM_END` / `STREAM_RESET` / `GO_AWAY` / `WINDOW_UPDATE` / `SETTINGS` / `PING` / `PUSH_PROMISE` / `PRIORITY_UPDATE`
- `h2.EventType`: `SESSION_READY` / `SESSION_CLOSED` / `SESSION_DRAINING` / `SESSION_REJECTED` / `STREAM_DATA` / `STREAM_RESET` / `STOP_SENDING` / `DATAGRAM` / `ERROR`

`Event` の主なフィールド: `quic.Event` は `stream_id` / `data` / `fin` / `error_code` / `reason` / `offset` (STREAM_DATA のストリーム上オフセット。他イベントでは 0)、`h3.Event` は `session_id` / `stream_id` / `data` / `error_code` / `error_message` / `is_unidirectional` (`is_unidirectional` は値が設定される経路が無く常に False)、`h2.Event` は `session_id` / `stream_id` / `data` / `error_code` / `error_message` / `fin` / `status_code` (SESSION_REJECTED でのみ意味を持つ。他イベントでは 0) / `headers` (SESSION_READY でのみ意味を持つ。疑似ヘッダー `:status` 等を含む。他イベントでは空)。`SESSION_REJECTED` は非 2xx 応答によるセッション拒否通知で、`SESSION_CLOSED` (確立後の終了) とは意味論が異なる。`http3.Event` は `stream_id` / `headers` / `data` / `error_code` / `push_id`、`http2.Event` は `stream_id` / `headers` / `data` / `error_code` / `last_stream_id` / `promised_stream_id` / `priority_field_value`。

## 注意点

- `verify_peer` のデフォルトが層で異なる。asyncio の `Client` は `verify_peer=True`、Sans I/O の `quic.Config` は `verify_peer=False`。Sans I/O API を直接使うときは明示的に有効にすること
- `open_stream()` の引数名とデフォルトが層で異なる。`quic` は `bidirectional: bool = True`、asyncio の `h3.Client` / `h2.Client` / `h2.SessionWriter` は `unidirectional: bool = False` (デフォルトは双方向)、asyncio の `h3.Server` は `unidirectional: bool = True` (デフォルトは単方向。双方向指定は `NotImplementedError`)。Sans I/O の `h3.Session` / `h2.Session` の `open_stream` はデフォルト値を持たず `is_unidirectional` を必ず指定する
- タイマー API (`get_timeout()` / `handle_timeout()`) があるのは `quic.Connection` のみ。`http3` / `h3` / `http2` / `h2` の Sans I/O クラスには無い
- 独自の例外クラスは `connect()` 失敗通知に限定して定義する (`WebTransportConnectError` と `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` の派生 3 クラス。asyncio の `h3` / `h2` の `Client.connect()` が送出する)。それ以外の生成系ファクトリの失敗は `RuntimeError`、asyncio ラッパーの未接続時操作も `RuntimeError` になる
- `webtransport.http2.ResponseWriter` はコールバック引数として渡されるが `http2/__init__.py` から再エクスポートされていない。型注釈で import する場合は `from webtransport.http2.server import ResponseWriter` を使う
- `h2.CapsuleType` (Capsule Protocol の型定数) も再エクスポートされていない。必要なら `from webtransport.webtransport_ext.h2 import CapsuleType` を使う

## サンプルコード

リポジトリの `examples/` に全モジュールの動くサンプルがある。

- `examples/webtransport/h3_server.py` / `h3_client.py`: WebTransport over HTTP/3 のエコーサーバーとクライアント
- `examples/webtransport/h2_server.py` / `h2_client.py`: WebTransport over HTTP/2 のエコーサーバーとクライアント
- `examples/quic/server.py` / `client.py`: QUIC 単体のエコーサーバーとクライアント
- `examples/http3/server.py` / `client.py`: HTTP/3 の最小サーバーと GET クライアント
- `examples/http2/server.py` / `client.py`: HTTP/2 の最小サーバーと GET クライアント
