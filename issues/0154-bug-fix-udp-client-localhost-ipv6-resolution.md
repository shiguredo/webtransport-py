# UDP 系の高レベルクライアント API が host="localhost" 指定で解決順先頭の family とソケット family が食い違う

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-udp-client-localhost-ipv6-resolution
- Polished: 2026-09-07

## 目的

`quic.Client` / `h3.Client` / `http3.Client` の各 asyncio ソケットは `AF_INET` 固定 (`socket.socket(socket.AF_INET, socket.SOCK_DGRAM)`) で作られる。一方で C++ の `fill_sockaddr` は `getaddrinfo(AF_UNSPEC)` の先頭要素を採用するため、macOS で `host="localhost"` を渡すと `::1` (IPv6) に解決され、`AF_INET` ソケットで `sock_sendto(("::1", port))` が `gaierror [Errno 8]` になる。`localhost` を既定とするクライアント examples が動作しない。本 issue の対象はクライアント側の解決のみとし、サーバーの dual-stack 化、C++ `fill_sockaddr` の数値専用化、IPv6 リテラル受理は対象外とする (いずれも原因・層の異なる別問題であり、必要になれば別途起票する)。

## 現状

- ソケットが `AF_INET` 固定の箇所は 7 箇所あるが、本 issue の対象はクライアント側 4 箇所 (`src/webtransport/quic/client.py` の `Client.connect` と `Client.migrate`、`src/webtransport/h3/client.py` の `Client.connect`、`src/webtransport/http3/client.py` の `Client.connect`) とする。サーバー 3 箇所 (`quic/server.py` / `h3/server.py` / `http3/server.py` の `Server.start`) は数値リテラル bind で名前解決を介さないため対象外である
- クライアントソケット生成は `AF_INET` + `("0.0.0.0", 0)` への bind を伴う。family 変更時は local bind アドレスも対応する family にする必要がある
- C++ の `src/bindings/quic.cpp` の `fill_sockaddr` は `getaddrinfo(hints.ai_family = AF_UNSPEC)` の先頭要素を採用
- 実験手順 (macOS。検証済みの方向): `examples/webtransport/h3_server.py` (0.0.0.0:4433、自己署名証明書を用意) を起動し、起動待機 (`wait_for` 等) 後に `examples/webtransport/h3_client.py` (既定 `url=https://localhost:4433/webtransport`) を実行すると `ConnectRefusedError: connection failed during establishment: [Errno 8] nodename nor servname provided, or not known` になる。対照として URL を `127.0.0.1` にすると往復成功する方向である (実行記録は未取得のため、実装時に再測定する)
- `getaddrinfo("localhost")` は macOS の環境では `::1` を先頭に返す (実行で確認済み。他環境では順序が異なる場合がある)
- h2 は `asyncio.open_connection` を使うため本問題の影響を受けない
- クライアント examples (`examples/webtransport/h3_client.py`、`examples/quic/client.py`) の既定値が `localhost` である。サーバー examples と README のサーバー例は `"0.0.0.0"` が既定であり本問題の対象外である

## 設計方針

- 名前解決を Python 側で `loop.getaddrinfo` により非同期で行い、得られた先頭 family でソケットを作る。先頭 family での接続に失敗した場合は次候補へ逐次フォールバックする (並列レースは行わない)
- 解決結果の文字列表現 (数値 IP) を C++ 側に渡す。`getaddrinfo` の戻りは既に文字列表現のため `inet_ntop` 変換は要らない。TLS の `server_name` とホスト名検証には元のホスト名を使い続け、数値化しない (証明書 SAN 検証の意味を変えないため)
- local bind は選択 family に対応させる (`AF_INET6` ソケットには `"::"` で bind する)
- `migrate` はリモート不変のため再解決せず、現接続と同一 family で新ソケットを作る
- C++ の `fill_sockaddr` の変更は行わない (数値受け渡しで従来動作のまま使えるため)。サーバーの dual-stack 化と IPv6 リテラル受理は対象外とする

## 完了条件

- `examples/webtransport/h3_client.py` (既定 `url=https://localhost:4433/webtransport`) が macOS でエラー無く接続完了すること
- QUIC 系 examples (`examples/quic/client.py` / `examples/quic/server.py` / `examples/http3/client.py` / `examples/http3/server.py` / `examples/webtransport/h3_client.py` / `examples/webtransport/h3_server.py` の 6 件。`examples/http3/client.py` の既定接続先 `www.google.com` は本検証の対象外とする) が既定引数で動作すること
- `tests/test_udp_resolution.py` を新規作成し、IPv4 / IPv6 の localhost 解決テストを追加すること。解決順は環境依存のため、順序自体ではなく family 一致での接続成功を表明する
- 既存のテスト全 834 件が引き続き通過すること
