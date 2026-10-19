# Chromium の WebTransport over HTTP/2 対応状況

- 調査日: 2026-10-19
- 結論: 最新の Chromium は WebTransport over HTTP/2 に対応していない。Chromium の WebTransport は HTTP/3 (QUIC) のみを使い、HTTP/2 へのフォールバックを行わない

## 確認方法

- `tests/browser/conftest.py` の `BrowserWebTransportServerH2` (WebTransport over HTTP/2 の echo サーバー) を起動する
- Playwright で各ブラウザを起動し、Shiguredo WebTransport DevTools (https://moqt-devtools.shiguredo.app/webtransport-devtools) の Connect ボタンから HTTP/2 サーバーへの接続を試みる一時的な pytest を作成して実行する
- 対照実験として、同じサーバーへ WebKit から接続する既存テスト (`tests/browser/test_webtransport_webkit_h2.py::test_browser_e2e_webtransport_h2`) を実行する
- 各ブラウザの起動時に NetLog (`--log-net-log`) を取得し、TCP / UDP / QUIC の接続試行を確認する

## 結果

| ブラウザ | バージョン | HTTP/2 サーバーへの接続 |
| --- | --- | --- |
| Playwright 同梱 Chromium | 151.0.7922.34 | 失敗 |
| Chrome 安定版 | 155.0.8059.80 | 失敗 |
| Chrome Dev | 158.0.8106.0 | 失敗 |
| Chrome Canary | 158.0.8110.0 | 失敗 |
| Playwright 同梱 WebKit | 26.5 | 成功 (既存テストが PASSED) |

- Chromium の失敗はすべて `net::ERR_CONNECTION_REFUSED` で、コンソールには `Failed to establish a connection to https://127.0.0.1:<port>/webtransport: net::ERR_CONNECTION_REFUSED.` が出力された
- NetLog には `QUIC_SESSION_WEBTRANSPORT_CLIENT_ALIVE` と `UDP_CONNECT` (127.0.0.1:<port>) が記録されたが、`TCP_CONNECT` は記録されなかった。Chromium は HTTP/2 (TCP) の接続を試行していない
- HTTP/2 サーバーは TCP で待ち受けているため、Chromium が HTTP/2 へフォールバックしていれば接続できるはずである。実際には QUIC (UDP) の接続拒否だけがエラーになった

## 根拠 (一次情報)

- Chrome 158 のリリースノートの "WebTransport reliability attributes" に「Chromium は現時点で HTTP/2 フォールバックに対応していない」と明記されている (https://chromestatus.com/release-notes/158)
- Chromium の main では `net/base/features.cc` と `chrome/browser/about_flags.cc` に WebTransport over HTTP/2 用のフラグが存在しない
- Chromium の `services/network/web_transport.cc` は QUICHE の QUIC セッションのみを扱う
- Chromium が利用する QUICHE には encapsulated WebTransport (HTTP/2 上で WebTransport を運ぶサーバー側実装) があるが、ブラウザの WebTransport API からは使われない

## DevTools の WebTransport API Support 表示との関係

- DevTools ページの "WebTransport API Support" はページ自身に "Evaluated on page load against WebTransport.prototype and related globals. Independent of connection state." と書かれている通り、API の存在確認だけを行う
- したがって全項目が OK (41 / 41) でも、WebTransport over HTTP/2 で接続できることは意味しない
- 手元のブラウザでページを開いた結果は Playwright 同梱 Chromium 151 が 23 / 41、Chrome 安定版 155 が 23 / 41、Chrome Canary 158 が 26 / 41 で、いずれも Partially Supported だった
- `--enable-experimental-web-platform-features` を付けて起動すると、Chrome 安定版 155 と Chrome Canary 158 のどちらも API Support は All Supported (41 / 41) になった。ただし `WebTransport.supportsReliableOnly` は `false` のままで、同じページから HTTP/2 サーバーへの接続は `net::ERR_CONNECTION_REFUSED` で失敗した。API 対応表の全 OK は実験フラグで API を有効化した結果であり、HTTP/2 対応とは無関係である
- 全項目 OK になるのは、実験フラグで API を有効化した場合のほか、API 面が揃ったブラウザ (ChromeStatus では残りの API の出荷が Chrome 159 として登録されている) の場合である
- `WebTransport.supportsReliableOnly` の存在だけでは HTTP/2 対応を判定できない。Chromium の main の実装は `false` を固定で返し、「Chromium は HTTP/3 で H3 Datagram を交渉した接続のみを確立する」とコメントしている (https://chromium.googlesource.com/chromium/src/+/main/third_party/blink/renderer/modules/webtransport/web_transport.cc#2943)
- 接続後に `reliability` が `supports-reliable-only` になるかが HTTP/2 接続の判定であり、DevTools の HTTP/2 バッジもこれを使う。Chromium は HTTP/3 接続でのみ `supports-unreliable` を返す

## 補足

- Firefox は WebTransport over HTTP/2 の実装を持つが未出荷である (https://bugzilla.mozilla.org/show_bug.cgi?id=1981483)
- Chromium の WebTransport API は HTTP/3 のみを使うため、`tests/browser/` の WebTransport over HTTP/2 テストは WebKit 専用のままとする
- 将来 Chromium が HTTP/2 フォールバックに対応した場合は、`tests/browser/test_webtransport_webkit_h2.py` と同じ形の Chromium 用テストを追加できる

## 再確認方法

- WebKit 側の対照実験: `uv run pytest tests/browser/test_webtransport_webkit_h2.py::test_browser_e2e_webtransport_h2 -m browser --timeout=120 -v`
- Chromium 側は `tests/browser/conftest.py` の `browser_server_h2` と `certificate_hash_value`、`tests/browser/helpers.py` の `_open_devtools` / `_wait_connected` を使って一時テストを作成し、NetLog を確認する
