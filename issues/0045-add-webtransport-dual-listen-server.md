# webtransport.Server を追加する（H3/H2 dual-listen glue）

- Created: 2026-08-07
- Completed:
- Branch: feature/add-webtransport-dual-listen-server
- Polished:

## 目的

現状 `webtransport.h3.Server` と `webtransport.h2.Server` は別モジュールに分かれており、ひとつのプロセスで H3 (UDP) と H2 (TCP) の両方を listen したい場合、ユーザーは自分で 2 つの Server を起動して 2 種類のコールバックを書き分ける必要がある。

`webtransport-py` の主要ユースケースはブラウザ → Python サーバー接続で、ブラウザ側は自前で H3/H2 の判定を持つため、サーバー側は「両プロトコル同時に listen して、来た方でハンドラを呼ぶ」ことができれば充分に価値がある。

本 issue はこれを実現する統合サーバー `webtransport.Server` を追加する。

## 現状

- `src/webtransport/__init__.py` は `quic`, `http2`, `http3`, `h3`, `h2` の 5 モジュールを公開するのみで、統合 Server クラスは無い。
- H3 / H2 両方 listen するには、ユーザーが `h3.Server` と `h2.Server` を 2 つ生成し、`asyncio.gather` で並行実行し、2 種類の Writer 型を扱うハンドラを書き分ける必要がある。
- 別 issue の「Server API を SessionWriter 型に統一する」で両サーバーのコールバック署名が揃うため、その完了後は 1 つのハンドラで両者を扱う薄い glue を提供できる。

一次資料の位置付けとしては `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 が「endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection」と述べ、`refs/webtrans/draft-ietf-webtrans-http2-15.txt` §1 が「the current most common TCP-based fallback to HTTP/3」と H2 版を H3 の TCP fallback として位置付けている。サーバーが両方を同時に受けられれば、クライアントが H3 で来ても H2 で来ても同一アプリケーションで処理できる。

## 設計方針

`h3.Server` と `h2.Server` を内部で保持し、両方の `run()` を `asyncio.gather` で同時実行する **薄い glue** を新規モジュールとして提供する。プロトコル固有のロジックは各下位 Server に閉じ込め、統合 Server 自身は listen ライフサイクルとハンドラの分配のみを担当する。

API 案:

```python
from webtransport import Server, SessionWriter

server = Server(
    host="0.0.0.0",
    port_h3=4433,           # UDP。None なら H3 を listen しない
    port_h2=8443,           # TCP。None なら H2 を listen しない
    certfile="cert.pem",
    keyfile="key.pem",
    allowed_origins=None,
    idle_timeout_ns=30_000_000_000,
)

@server.on_session_ready
async def _(writer: SessionWriter) -> None:
    # writer.transport は "h3" または "h2"
    await writer.send_datagram(b"hello")

await server.serve()  # 2 つの内部サーバーを並行起動、両方停止するまで await
```

方針の要点:

- `port_h3` / `port_h2` のどちらかを `None` にすると片系のみ listen する（両方 `None` は起動時 `ValueError`）。
- 証明書 / 秘密鍵 / `allowed_origins` / `idle_timeout_ns` は両サーバーで共通に使う。
- コールバック登録メソッド（`on_session_ready`, `on_session_closed`, `on_stream_data`, `on_stream_reset`, `on_datagram`）は登録時に内部の 2 サーバーに同じ関数を渡す。
- `serve()` は内部 2 サーバーの `run()` を `asyncio.gather` で並行実行し、どちらかがエラー終了したら残りも停止させる（`asyncio.CancelledError` の伝播で明示的にキャンセル）。
- `SessionWriter` は先行 issue で `src/webtransport/_common.py` に定義された `Protocol`。統合 Server はこれを再エクスポートするだけ。

## 完了条件

- `webtransport.Server`, `webtransport.SessionWriter` が `from webtransport import Server, SessionWriter` で利用できる
- 単一プロセスで H3 (UDP) と H2 (TCP) を同一の cert / key で並行 listen し、同一のハンドラ関数が両プロトコル由来の Writer を受け取れる
- `port_h3=None` または `port_h2=None` で片系のみ listen 可能
- 両方 `None` の指定は `ValueError` で拒否される
- `examples/webtransport/dual_server.py` が `h3_client.py` と `h2_client.py` の両方から接続を受けて動作する
- 新設テスト `tests/test_e2e_webtransport_dual.py` が同一プロセスで両プロトコル接続を検証する

## 解決方法

対象ファイル:

- `src/webtransport/__init__.py`（`Server`, `SessionWriter` を追加公開）
- `src/webtransport/server.py`（新設。統合 Server 実装）
- `src/webtransport/_common.py`（先行 issue で新設済み。ここでは変更しない想定）
- `examples/webtransport/dual_server.py`（新設）
- `tests/test_e2e_webtransport_dual.py`（新設。同一プロセスで h3 / h2 両方に接続してハンドラが同じ Writer 型を受けることを検証）
- `skills/webtransport-py/SKILL.md`（統合 Server 節を追加）

## 検証

- `uv run pytest tests/test_e2e_webtransport_dual.py` が新規シナリオで通ることを確認する
- `uv run pytest tests/` 全体でリグレッションが無いことを確認する
- `examples/webtransport/dual_server.py` を起動し、`examples/webtransport/h3_client.py` と `examples/webtransport/h2_client.py` の両方から接続できることを手動で確認する
- Chromium で `examples/webtransport/dual_server.py` の H3 ポートに接続できることを、既存の実ブラウザ E2E ハーネス（closed issue 0004 で導入されたもの）を使って確認する

## 依存関係

- 「Server API を SessionWriter 型に統一する」issue の完了に依存する。両サーバーの Writer 型が揃わないと、統合 Server のハンドラが 1 種類の型で書けない。
- 本 issue の完了は「`webtransport.Client`（transport 明示指定）」issue の共通テストファイル `tests/test_e2e_webtransport_dual.py` の存在前提となる（同ファイルは本 issue で新設し、後続 issue で追記する）。

## 参考

- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 "endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` §1 "the current most common TCP-based fallback to HTTP/3"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` Appendix A "endpoints SHOULD prefer [version-specific WebTransport protocol] over the capsule-based protocol"
