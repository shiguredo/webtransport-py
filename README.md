# webtransport-py

[![PyPI](https://img.shields.io/pypi/v/webtransport-py)](https://pypi.org/project/webtransport-py/)
[![image](https://img.shields.io/pypi/pyversions/webtransport-py.svg)](https://pypi.python.org/pypi/webtransport-py)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Actions status](https://github.com/shiguredo/webtransport-py/workflows/wheel/badge.svg)](https://github.com/shiguredo/webtransport-py/actions)

## About Shiguredo's open source software

We will not respond to PRs or issues that have not been discussed on Discord. Also, Discord is only available in Japanese.

Please read <https://github.com/shiguredo/oss/blob/master/README.en.md> before use.

## 時雨堂のオープンソースソフトウェアについて

利用前に <https://github.com/shiguredo/oss> をお読みください。

## webtransport-py について

webtransport-py は Sans I/O アーキテクチャを採用した WebTransport の Python ライブラリです。WebTransport over HTTP/3 と WebTransport over HTTP/2 の両方に対応しています。

また、WebTransport だけでなく QUIC、HTTP/3、HTTP/2 を単体のプロトコルとしても利用できます。asyncio、スレッド、独自のイベントループなど、任意の I/O フレームワークと組み合わせて利用できます。

## 特徴

- Sans I/O アーキテクチャ
  - I/O 処理をライブラリ外部で制御可能
  - 任意のイベントループやフレームワークと統合可能
  - [Sans I/O](https://sans-io.readthedocs.io/)
- 二層 API 設計
  - Sans I/O API: プロトコル処理のみを提供する低レベル API
  - asyncio API: すぐに使える高レベルなクライアント/サーバー実装
- QUIC
  - Sans I/O API と asyncio API の両方を提供
  - 双方向/単方向ストリーム
  - QUIC DATAGRAM
  - 0-RTT / Session Resumption
  - 証明書のカスタム検証
  - [ngtcp2](https://github.com/ngtcp2/ngtcp2) を採用
- HTTP/3
  - Sans I/O API と asyncio API の両方を提供
  - [nghttp3](https://github.com/ngtcp2/nghttp3) を採用
- HTTP/2
  - Sans I/O API と asyncio API の両方を提供
  - [nghttp2](https://github.com/nghttp2/nghttp2) を採用
- WebTransport over HTTP/3
  - Sans I/O API と asyncio API の両方を提供
- WebTransport over HTTP/2
  - Sans I/O API と asyncio API の両方を提供
- Python [Free-Threading](https://docs.python.org/3/howto/free-threading-python.html) 対応
  - [PEP 703 – Making the Global Interpreter Lock Optional in CPython \| peps\.python\.org](https://peps.python.org/pep-0703/)
  - [Python Free\-Threading Guide](https://py-free-threading.github.io/)
- クロスプラットフォーム対応
  - Ubuntu x86_64 / arm64
  - macOS arm64

## インストール

```bash
uv add webtransport-py
```

## 使い方

### WebTransport over HTTP/3

#### サーバー

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

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        print(f"セッション確立: {session_id} from {addr}")

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        print(f"データ受信: {data}")
        # エコーバック
        await server.send_stream_data(addr, stream_id, data)

    async def on_datagram(session_id: int, data: bytes, addr: tuple[str, int]) -> None:
        print(f"データグラム受信: {data}")
        # エコーバック
        await server.send_datagram(addr, session_id, data)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    async with server:
        print(f"サーバー開始: {server.host}:{server.actual_port}")
        await server.run()


if __name__ == "__main__":
    asyncio.run(main())
```

#### クライアント

```python
import asyncio

from webtransport import h3


async def main() -> None:
    client = h3.Client(
        url="https://localhost:4433/webtransport",
        verify_peer=False,
    )

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        print(f"データ受信: {data}")

    async def on_datagram(data: bytes) -> None:
        print(f"データグラム受信: {data}")

    client.on_stream_data(on_stream_data)
    client.on_datagram(on_datagram)

    if not await client.connect():
        print("接続失敗")
        return

    # ストリームでデータ送信
    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"Hello via stream!")

    # データグラムでデータ送信
    await client.send_datagram(b"Hello via datagram!")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

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

    async def on_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        print(f"データ受信: {data}")

    client.on_stream_data(on_stream_data)

    if not await client.connect():
        print("接続失敗")
        return

    # 双方向ストリームを開いてデータ送信
    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"Hello, QUIC!")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

## Python

- 3.14
- 3.14t

## プラットフォーム

- Ubuntu 24.04 LTS x86_64
- Ubuntu 24.04 LTS arm64
- macOS 26 arm64

## リリースビルド

```bash
make wheel
```

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

## ngtcp2 ライセンス

<https://github.com/ngtcp2/ngtcp2/blob/main/COPYING>

```text
The MIT License

Copyright (c) 2016 ngtcp2 contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## nghttp3 ライセンス

<https://github.com/ngtcp2/nghttp3/blob/main/COPYING>

```text
The MIT License

Copyright (c) 2019 nghttp3 contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## nghttp2 ライセンス

<https://github.com/nghttp2/nghttp2/blob/master/COPYING>

```text
The MIT License

Copyright (c) 2012, 2014, 2015, 2016 Tatsuhiro Tsujikawa
Copyright (c) 2012, 2014, 2015, 2016 nghttp2 contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

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
