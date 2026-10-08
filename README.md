# webtransport-py

[![PyPI](https://img.shields.io/pypi/v/webtransport-py)](https://pypi.org/project/webtransport-py/)
[![image](https://img.shields.io/pypi/pyversions/webtransport-py.svg)](https://pypi.python.org/pypi/webtransport-py)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Actions status](https://github.com/shiguredo/webtransport-py/workflows/wheel/badge.svg)](https://github.com/shiguredo/webtransport-py/actions)
[![Discord](https://img.shields.io/badge/Discord-%235865F2.svg?logo=discord&logoColor=white)](https://discord.gg/shiguredo)

## About Shiguredo's open source software

We will not respond to PRs or issues that have not been discussed on Discord. Also, Discord is only available in Japanese.

Please read <https://github.com/shiguredo/oss/blob/master/README.en.md> before use.

## 時雨堂のオープンソースソフトウェアについて

利用前に <https://github.com/shiguredo/oss> をお読みください。

## webtransport-py について

webtransport-py は Sans I/O アーキテクチャを採用した WebTransport の Python ライブラリです。WebTransport over HTTP/3 と WebTransport over HTTP/2 の両方に対応しています。

また、WebTransport だけでなく QUIC、HTTP/3、HTTP/2 を単体のプロトコルとしても利用できます。QMux の Sans I/O API も提供しています。asyncio、スレッド、独自のイベントループなど、任意の I/O フレームワークと組み合わせて利用できます。

## 特徴

- Sans I/O アーキテクチャ
  - I/O 処理をライブラリ外部で制御可能
  - 任意のイベントループやフレームワークと統合可能
  - [Sans I/O](https://sans-io.readthedocs.io/)
- 二層 API 設計
  - Sans I/O API: プロトコル処理のみを提供する低レベル API
  - asyncio API: すぐに使える高レベルなクライアント/サーバー実装
- WebTransport over HTTP/3
  - Sans I/O API と asyncio API の両方を提供
- WebTransport over HTTP/2
  - Sans I/O API と asyncio API の両方を提供
- QUIC
  - Sans I/O API と asyncio API の両方を提供
  - 双方向/単方向ストリーム
  - QUIC DATAGRAM
  - 0-RTT / Session Resumption
  - 証明書のカスタム検証
  - Connection Migration
  - [ngtcp2](https://github.com/ngtcp2/ngtcp2) を採用
- HTTP/3
  - Sans I/O API と asyncio API の両方を提供
  - [nghttp3](https://github.com/ngtcp2/nghttp3) を採用
- HTTP/2
  - Sans I/O API と asyncio API の両方を提供
  - [nghttp2](https://github.com/nghttp2/nghttp2) を採用
- QMux
  - 任意の双方向バイトストリーム上でストリーム多重化を提供
  - Sans I/O API のみを提供 (asyncio API は未対応)
  - [dwnx](https://github.com/ngtcp2/dwnx) を採用
  - DATAGRAM は未対応
- 依存ライブラリは [deps.json](deps.json) で特定のタグ / コミットに固定する
  - ngtcp2 / nghttp3 / dwnx は上流ブランチの特定コミット (`ref`)、nghttp2 / AWS-LC はタグ (`tag`) で固定する
  - 更新時は `deps.json` を書き換える (ビルドキャッシュのキーも `deps.json` の内容に連動する)
- Python [Free-Threading](https://docs.python.org/3/howto/free-threading-python.html) 対応
  - [PEP 703 – Making the Global Interpreter Lock Optional in CPython \| peps\.python\.org](https://peps.python.org/pep-0703/)
  - [Python Free\-Threading Guide](https://py-free-threading.github.io/)
  - 同一オブジェクトへの並行アクセスはオブジェクト単位の排他で保護する (接続・セッション系クラスの公開メソッドが対象。生成系 static・設定・イベント等の値オブジェクトは対象外)。Config は単一スレッドで構築し、共有後の並行書き換えは行わないこと。quic.Config の検証コールバック内で同一 quic.Connection のメソッドを呼ばないこと
- クロスプラットフォーム対応
  - Ubuntu x86_64 / arm64
  - macOS arm64

## インストール

```bash
uv add webtransport-py
```

## 使い方

### WebTransport

`HTTPVersion` で HTTP/2 (TCP + TLS) と HTTP/3 (UDP + QUIC) を選ぶ。既定は HTTP/3。

```python
import asyncio

from webtransport import Client, HTTPVersion, Server, Session


async def main() -> None:
    server = Server(
        host="0.0.0.0",
        port=4433,
        http_version=HTTPVersion.HTTP3,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_datagram(session: Session, data: bytes) -> None:
        # セッションハンドル経由で返す
        await session.send_datagram(data)

    server.on_datagram(on_datagram)

    async with server:
        await server.run()


asyncio.run(main())
```

```python
import asyncio

from webtransport import Client, HTTPVersion, WebTransportError


async def main() -> None:
    client = Client(
        url="https://localhost:4433/webtransport",
        http_version=HTTPVersion.HTTP3,
        verify_peer=False,
    )

    async def on_datagram(data: bytes) -> None:
        print(f"データグラム受信: {data}")

    client.on_datagram(on_datagram)

    try:
        await client.connect()
    except WebTransportError as exc:
        print(f"接続失敗: {exc}")
        await client.close()
        return

    await client.send_datagram(b"Hello, WebTransport!")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()


asyncio.run(main())
```

`http_version` に `HTTPVersion.HTTP2` を渡すと WebTransport over HTTP/2 になる (`certfile` / `keyfile` が必須)。プロトコル固有の API は `client.h3` / `client.h2` / `server.h3` / `server.h2` から呼ぶ。

低レベル (Sans-IO) API と全 API のリファレンスは `skills/webtransport-py/SKILL.md` を参照。

### QUIC

#### サーバー

```python
import asyncio

from webtransport import quic


async def main() -> None:
    server = quic.Server(
        host="0.0.0.0",
        port=4433,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        print(f"ハンドシェイク完了: {addr}")

    async def on_stream_data(
        stream_id: int,
        data: bytes,
        fin: bool,
        addr: tuple[str, int],
    ) -> None:
        print(f"データ受信: {data}")
        # エコーバック
        await server.send_stream_data(addr, stream_id, data, fin)

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)

    async with server:
        print(f"サーバー開始: {server.host}:{server.actual_port}")
        await server.run()


if __name__ == "__main__":
    asyncio.run(main())
```

#### クライアント

```python
import asyncio

from webtransport import quic


async def main() -> None:
    client = quic.Client(
        host="localhost",
        port=4433,
        verify_peer=False,
    )

    if not await client.connect():
        print("接続失敗")
        return

    # 双方向ストリームを開いてデータ送信 (FIN でストリームを閉じる)
    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"Hello, QUIC!", fin=True)

    # サーバーからのエコーを FIN まで受信する
    try:
        data, _ = await client.recv_stream_data(stream_id, timeout=5.0)
        print(f"データ受信: {data}")
    except TimeoutError:
        print("受信タイムアウト")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### QMux

QMux (draft-ietf-quic-qmux) は任意の双方向バイトストリーム上でストリーム多重化を提供するプロトコルです。Sans I/O API のみを提供します。

```python
from webtransport import qmux

config = qmux.Config()
client = qmux.Connection.create_client(config)
server = qmux.Connection.create_server(config)

# 受け取ったバイト列を渡し、送信すべきレコードを取り出す
client.receive(record_from_peer)
record = client.pending_record
```

レコード (varint の長さ + QUIC フレーム列) をバイト列として入出力します。トランスポートパラメータは帯域内で交換するため、TLS が無くても 2 つの接続をバイト列で直結すればハンドシェイクできます。

## Python

- 3.14
- 3.14t

## プラットフォーム

- Ubuntu 26.04 LTS x86_64
- Ubuntu 26.04 LTS arm64
- Ubuntu 24.04 LTS x86_64
- Ubuntu 24.04 LTS arm64
- macOS 26 arm64

## リリースビルド

```bash
make wheel
```

リリース時は `CHANGES.md` の `## develop` を `## <バージョン>` に変更し、`**リリース日**: YYYY-MM-DD` を追記する (`VERSION` の更新と `make wheel` によるビルドも併せて行う)。

## 開発ビルド

```bash
make develop
```

## テスト

```bash
uv sync
make test
```

## サンプル

[examples/](examples/) ディレクトリにサンプルコードがあります。

## 第三者ライセンス

本プロジェクトは ngtcp2 / nghttp3 / nghttp2 / dwnx / AWS-LC / nanobind (同梱の tsl::robin_map を含む) を静的リンクしています。各ライセンス全文は [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) を参照してください。

## ライセンス

Apache License 2.0

```text
Copyright 2026 Shiguredo Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
