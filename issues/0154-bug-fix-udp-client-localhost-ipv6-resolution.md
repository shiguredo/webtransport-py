# UDP 系の高レベル API が host="localhost" 指定で macOS の IPv6 解決に失敗する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-udp-client-localhost-ipv6-resolution
- Polished: {YYYY-MM-DD}

## 目的

`quic.Client` / `h3.Client` / `http3.Client` の各 asyncio ソケットは `AF_INET` 固定 (`socket.socket(socket.AF_INET, socket.SOCK_DGRAM)`) で作られる。一方で C++ の `fill_sockaddr` は `getaddrinfo(AF_UNSPEC)` の先頭要素を採用するため、macOS で `host="localhost"` を渡すと `::1` (IPv6) に解決され、`AF_INET` ソケットで `sock_sendto(("::1", port))` が `gaierror [Errno 8]` になる。README・SKILL.md・examples が既定で `localhost` を使うため、examples の QUIC 系が全滅する。同じ問題は Server 側の bind (`AF_INET` + `"0.0.0.0"`) にもあり、IPv6 環境で聞けない。

## 現状

- ソケットが `AF_INET` 固定の箇所: `src/webtransport/quic/client.py` の `Client.connect` と `Client.migrate`、`src/webtransport/quic/server.py` の `Server.start`、`src/webtransport/h3/client.py` の `Client.connect`、`src/webtransport/h3/server.py` の `Server.start`、`src/webtransport/http3/client.py` の `Client.connect`、`src/webtransport/http3/server.py` の `Server.start` (計 7 箇所)
- C++ の `src/bindings/quic.cpp` の `fill_sockaddr` は `getaddrinfo(hints.ai_family = AF_UNSPEC)` の先頭要素を採用
- 実験 (macOS): `examples/webtransport/h3_server.py` (0.0.0.0:4433) + `examples/webtransport/h3_client.py` (既定 `url=https://localhost:4433/webtransport`) を実行すると `ConnectRefusedError: connection failed during establishment: [Errno 8] nodename nor servname provided, or not known`
- 対照実験: URL を `127.0.0.1` にすると往復成功 (フルスタックは正常動作)
- `getaddrinfo("localhost")` は macOS の環境では `::1` を先頭に返す
- h2 は `asyncio.open_connection` (Happy Eyeballs 対応) を使うため本問題の影響を受けない
- README (h3 サーバー例)、examples/webtransport/h3_client.py と h3_server.py、examples/quic/client.py と server.py の既定値が全て `localhost`

## 設計方針

- 名前解決を Python 側で `loop.getaddrinfo` により非同期で行い、得られた family でソケットを作る
- 得られたアドレスを数値表現 (`socket.inet_ntop`) で C++ 側に渡すことで、C++ 側の同期 `getaddrinfo` (イベントループをブロックする) の使用も止める
- C++ の `fill_sockaddr` は数値専用にし (`inet_pton` のみ)、ホスト名解決の責務を Python 側に一本化する
- サーバーの bind 側も同型に変える (`AF_UNSPEC` で解決、または `AF_INET6` + `IPV6_V6ONLY=0` でデュアルスタック)
- 対処後、examples の既定値は `localhost` のままで動くようにする

## 完了条件

- `examples/webtransport/h3_client.py` (既定 `url=https://localhost:4433/webtransport`) が macOS でエラー無く接続完了すること
- QUIC 系の 6 examples が既定引数で動作すること
- IPv6 リテラル (`[::1]` を含む URL) が受理されること
- `tests/` に IPv4 / IPv6 の localhost 解決テスト、IPv6 リテラル URL の解析テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
