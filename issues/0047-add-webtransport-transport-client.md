# webtransport.Client を追加する（transport 明示指定）

- Created: 2026-08-07
- Completed:
- Branch: feature/add-webtransport-transport-client
- Polished:

## 目的

現状 `webtransport.h3.Client` と `webtransport.h2.Client` は別モジュールに分かれており、ユーザーは接続したいプロトコルごとに import 先と型を選ぶ必要がある。統合 Server（別 issue）と対になるクライアントエントリポイントとして `webtransport.Client(url, transport="h3"|"h2")` を提供し、単一の import と単一の型で両プロトコルを扱えるようにする。

本 issue の範囲は **明示指定のみ**。`"auto"` フォールバック、origin キャッシュ、SVCB / Alt-Svc / Happy Eyeballs はいずれも v1 では明示的な non-goal とする（理由は後述）。

## 現状

- `src/webtransport/__init__.py` は `quic`, `http2`, `http3`, `h3`, `h2` の 5 モジュールを公開するのみで、統合 Client クラスは無い。
- 統合 Server 側では `webtransport.Server` を先行 issue で導入するため、統合 Client も対で欲しいというユーザー要望がある。
- 別 issue の「Client.connect の無制限待機ループを修正して bounded にする」で `connect(timeout: float)` が bounded になり例外を送出する形に統一されるため、統合 Client 側も同じシグネチャに揃えられる。

一次資料の位置付けとしては `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 が「endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection」、`refs/webtrans/draft-ietf-webtrans-http2-15.txt` §1 が「the current most common TCP-based fallback to HTTP/3」と述べており、両プロトコル間の選択が発生することは仕様側でも想定されている。ただし選択アルゴリズム自体は WebTransport ドラフト内で規定されておらず、HTTP レイヤ（Alt-Svc / SVCB）や TCP 併用の選択機構（Happy Eyeballs）に委ねられている。

## 設計方針

`h3.Client` / `h2.Client` に委譲する **薄いディスパッチ層** として `webtransport.Client` を導入する。プロトコル選択は明示指定のみ。

API 案:

```python
from webtransport import Client

# 明示指定のみ。"auto" は v1 では実装しない
client = Client(url="https://example.com:4433/wt", transport="h3")
await client.connect(timeout=10.0)
```

- `transport="h3"` または `transport="h2"` のみを受け付ける。それ以外の値は `__init__` で `ValueError` を送出する。
- `transport="auto"` を明示的に渡した場合は、`NotImplementedError("auto transport selection is not implemented yet; specify 'h3' or 'h2'")` を送出する。将来のフォールバック実装時に、この分岐を差し替える形にする（本 issue では未実装であることをコード上明示する）。
- 内部で `h3.Client` または `h2.Client` を保持し、以下のメソッド・コールバックをそのまま委譲する。
  - `connect(timeout)`, `open_stream`, `send_stream_data`, `send_datagram`, `reset_stream`, `close_stream`, `run`, `close`
  - `on_session_ready`, `on_session_closed`, `on_stream_data`, `on_stream_reset`, `on_datagram`
  - `__aenter__` / `__aexit__`
- `Client.transport` プロパティで `"h3"` / `"h2"` を参照できるようにする。
- `verify_peer`, `origin`, `idle_timeout_ns` などのコンストラクタ引数はプロトコル間の差異を吸収する。`h2.Client` が持たない `idle_timeout_ns` / `ca_file` / `verify_callback` は `transport="h2"` の場合に指定されていたら `TypeError` を送出する（または将来の h2 拡張を見据えて無視して警告するかは実装時に検討する）。

## Non-goals（v1 で明示的に入れない）

以下はいずれもコード内コメント / `SKILL.md` / `CHANGES.md` に「非対応」と明記する。

1. **`transport="auto"` の H3→H2 自動フォールバック**: 本 issue では `NotImplementedError` を送出するのみ。将来別 issue で扱う。推奨実装は sequential-with-deadline（H3 で bounded connect → タイムアウトしたら H2 に切り替え）で、race は teardown 経路の複雑さに見合わない見立て。
2. **origin キャッシュ**: このライブラリには HTTP クライアントスタックと origin 状態を保持する場所が無い。実装するには HTTP スタック丸ごとの導入が必要になる。Python-Python シナリオが立ち上がってから設計する。
3. **HTTPS/SVCB DNS 発見（RFC 9460）**: Python 標準の `socket` に SVCB サポートが無く、`dnspython` 依存追加は重い。Python-Python シナリオでニーズが実証されてから検討する。
4. **Alt-Svc（RFC 7838）解釈**: 上記 2 の派生。HTTP クライアントスタックが前提となる。
5. **Happy Eyeballs QUIC race（RFC 8305 系）**: 上記 1 の派生。両候補を並行維持する状態機械が新規に必要になる。

これら 5 項目は、Alt-Svc / SVCB / Happy Eyeballs の RFC 一次資料が `refs/` に置かれていないことも理由の 1 つ。将来これらを扱う際は `/update-refs` で先に一次資料を追加してから設計する。

## 完了条件

- `from webtransport import Client` で統合 Client が使える
- `Client(url, transport="h3")` / `Client(url, transport="h2")` がそれぞれ `h3.Client` / `h2.Client` と同等の挙動を示す（`connect`, `send_datagram`, `run`, `close`, 各種 `on_*` コールバック）
- `transport` に `"h3"` / `"h2"` 以外を渡すと `ValueError` が送出される
- `transport="auto"` を明示的に渡すと `NotImplementedError` が送出される
- Non-goals 5 項目が `src/webtransport/client.py` の docstring / `skills/webtransport-py/SKILL.md` / `CHANGES.md` に明記されている
- 追加テスト `tests/test_e2e_webtransport_dual.py` に、統合 Client で h3 / h2 それぞれの統合 Server に接続できることを検証するケースが追加されている

## 解決方法

対象ファイル:

- `src/webtransport/__init__.py`（`Client` の追加公開）
- `src/webtransport/client.py`（新設。ディスパッチ層）
- `examples/webtransport/dual_client.py`（新設。統合 Client で h3 / h2 の両方を明示指定で試す例）
- `tests/test_e2e_webtransport_dual.py`（先行 issue で新設済み。統合 Client のケースを追加）
- `skills/webtransport-py/SKILL.md`（統合 Client 節と Non-goals の記述を追加）

## 検証

- `uv run pytest tests/test_e2e_webtransport_dual.py` の新規ケースが通ることを確認する
- `uv run pytest tests/` 全体でリグレッションが無いことを確認する
- `examples/webtransport/dual_server.py`（先行 issue で新設）を起動し、`examples/webtransport/dual_client.py` で `transport="h3"` と `transport="h2"` の両方の接続が成功することを手動で確認する
- `transport="auto"` を渡した場合の `NotImplementedError` メッセージがユーザーに「明示指定してほしい」ことを伝える文言になっているか確認する

## 依存関係

- 「Client.connect の無制限待機ループを修正して bounded にする」issue の完了に依存する。統合 Client の `connect(timeout)` は下位 Client の同シグネチャに委譲するため、bounded connect が入っていないとタイムアウト指定が意味を持たない。
- 「`webtransport.Server`（dual-listen glue）の追加」issue の完了に依存する（統合 Client のテストは統合 Server と対で行うため、`tests/test_e2e_webtransport_dual.py` を共有する）。
- 「Server API を SessionWriter 型に統一する」issue の完了そのものには直接依存しないが、実質的には上記 2 issue を経由する形で依存する。

## 参考

- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 "endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` §1 "the current most common TCP-based fallback to HTTP/3"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` Appendix A "endpoints SHOULD prefer [version-specific WebTransport protocol] over the capsule-based protocol"

## pending にした理由

本 issue は h2/h3 統一 listen（`webtransport.Server` dual-listen glue）関連の実装群（0044-0047）の一部であり、その実装を一旦後回しにすることにしたため、保留する。実装再開時に reopened にする。
