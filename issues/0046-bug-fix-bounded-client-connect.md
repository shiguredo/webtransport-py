# Client.connect の無制限待機ループを修正して bounded にする

- Created: 2026-08-07
- Completed:
- Branch: feature/fix-bounded-client-connect
- Polished:

## 目的

`h3.Client.connect()` および `h2.Client.connect()` の内部待機ループが実質無制限になっており、UDP blackhole や TCP 応答なしといった片方向遮断状況下でハンドラを無限にブロックする。加えて戻り値が `bool` のため「サーバー拒否」と「ネットワーク的な到達失敗」を区別できず、呼び出し側が後段でリトライや fallback を判定する余地が無い。

本 issue はこの独立バグを修正し、`connect()` が指定タイムアウト内に成功／失敗を必ず返すようにして、失敗理由を具体例外で伝える。

## 現状

### h3.Client.connect の待機ループ

`src/webtransport/h3/client.py` の `Client.connect` 内に 2 つの無制限ループが存在する。

1. HANDSHAKE 完了待ちループ: `while not handshake_done and self._running:` で回り続ける。ループ内の `_receive` は `asyncio.wait_for` で 0.1 秒タイムアウトを持つが、それを `except TimeoutError: pass` で吸収するため、UDP パケットが 1 つも返ってこない場合はこのループが永久に回る。`self._running` は接続イベント（`CONNECTION_CLOSED`）でしか `False` にならず、外部からのタイムアウト到達手段が無い。
2. SETTINGS 受信待ちループ: `while not settings_received and self._running and attempt < max_attempts:` で `max_attempts = 100` と `asyncio.sleep(0.01)` の組み合わせにより約 11 秒でループを抜けるが、この定数はユーザーから見えず設定できず、下限が固定である。

### h2.Client.connect の待機ループ

`src/webtransport/h2/client.py` の `Client.connect` 内に、200 OK 応答待ちループ `while self._running:` が存在する。`asyncio.open_connection` は OS デフォルトのタイムアウトに委ねる（実質無制限）。ループ側もサーバーがイベントを返さない限り抜ける機構が無く、無応答時に永久ループとなる。`_wait_webtransport_ready` は `timeout_seconds` 引数を持ち bounded になっているが、その後の 200 OK 待ちが bounded ではない。

### 戻り値の情報量不足

いずれの `connect()` も戻り値は `bool` で、成功時 `True` / 失敗時 `False` を返す。呼び出し側は失敗理由（タイムアウト、TCP RST、TLS 失敗、サーバー拒否、Extended CONNECT 拒否など）を区別できない。将来 H3 → H2 の自動 fallback を検討する際にも、失敗理由の区別は判定の前提となる。

一次資料としては `refs/h3/rfc9114.txt` §3.1 が「Connectivity problems (e.g., blocking UDP) can result in a failure to establish a QUIC connection; clients SHOULD attempt to use TCP-based versions of HTTP in this case」と述べているが、これを実装する前段として、そもそも「QUIC 到達失敗の判定」自体が bounded に完了する必要がある。

## 設計方針

`connect()` を deadline ベースで bounded にし、失敗を具体例外で通知する。

- `Client.connect(timeout: float = 10.0) -> None` に統一する。成功時は `None` を返し、失敗時は下記の具体例外を送出する。既存の `bool` 戻り値は廃止する。
- deadline は `asyncio.get_running_loop().time() + timeout` で 1 度計算し、`connect()` 内のすべての待機ループがこの同一 deadline を参照する。ループ本体は `while loop.time() < deadline` で判定する。
- 例外階層は `src/webtransport/exceptions.py` を新設して以下を定義する。すべて `WebTransportConnectError` を親に持つ。
  - `ConnectTimeoutError`: 指定 `timeout` を超えても handshake / SETTINGS / 200 OK が完了しなかった場合
  - `ConnectRefusedError`: TCP RST や QUIC 側で明示的な `CONNECTION_CLOSE` を受けた場合
  - `HandshakeFailedError`: TLS ハンドシェイクや Extended CONNECT のセマンティック失敗（非 2xx 応答など）
- `h3.Client` 内では HANDSHAKE 待ち / SETTINGS 待ちの両ループを同一 deadline で制御し、SETTINGS 側の `max_attempts = 100` 固定は廃止する。
- `h2.Client` 内では 200 OK 待ちループを deadline 制御に切り替え、`_wait_webtransport_ready` の `timeout_seconds` にも同じ deadline から算出した残り時間を渡す。
- `asyncio.open_connection` は `asyncio.wait_for(..., timeout=remaining)` でラップし、接続段階もタイムアウトさせる。

破壊的変更として、既存の `connect()` の `bool` 戻り値に依存するテスト・examples は例外ハンドリングに書き換える。`CHANGES.md` に `shiguredo-changelog` スキルの規約に沿って `[CHANGE]` / `[FIX]` のいずれか妥当な種別で記載する。

## 完了条件

- `h3.Client.connect(timeout=1.0)` を、応答しない UDP ポート（例: 127.0.0.1:1）に対して呼ぶと、1 秒強で `ConnectTimeoutError` が送出される
- `h2.Client.connect(timeout=1.0)` を、応答しない TCP ポート（例: 127.0.0.1:1）に対して呼ぶと、1 秒強で `ConnectTimeoutError` が送出される
- `h3.Client.connect()` と `h2.Client.connect()` の戻り値がなくなり、成功は例外なしの正常復帰、失敗は具体例外による通知に統一される
- `src/webtransport/exceptions.py` に `WebTransportConnectError` / `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` が定義され、`webtransport` トップレベルから import できる
- `h3.Client` の SETTINGS 待ちで使われていた `max_attempts = 100` の固定値が削除されている
- 既存の `tests/test_e2e_webtransport_h3.py` / `tests/test_e2e_webtransport_h2.py` / `examples/webtransport/h3_client.py` / `examples/webtransport/h2_client.py` が新シグネチャに追従している

## 解決方法

対象ファイル:

- `src/webtransport/h3/client.py`（`connect` シグネチャ、待機ループの deadline 化）
- `src/webtransport/h2/client.py`（`connect` シグネチャ、200 OK 待ちの deadline 化、`asyncio.open_connection` のタイムアウトラップ）
- `src/webtransport/exceptions.py`（新設）
- `src/webtransport/__init__.py`（例外クラスの再エクスポート）
- `examples/webtransport/h3_client.py`（例外ハンドリングに書き換え）
- `examples/webtransport/h2_client.py`（同上）
- `tests/test_e2e_webtransport_h3.py`（成功時は `await client.connect()`、失敗時は `pytest.raises(...)` の形に）
- `tests/test_e2e_webtransport_h2.py`（同上）
- 新設テスト（Blackhole ポートに対する `ConnectTimeoutError` 送出を検証するテストケースを既存の E2E テストに追加する。または新規ファイル `tests/test_client_connect_timeout.py` として切り出す）
- `skills/webtransport-py/SKILL.md`（Client 節の `connect` 記述を更新）
- `CHANGES.md`（`[CHANGE]` / `[FIX]` エントリを追加）

## 検証

- `uv run pytest tests/` を通す
- `uv run pytest tests/prop_webtransport_h3.py tests/prop_webtransport_h2.py` の Hypothesis プロパティテストがリグレッションしないことを確認する
- UDP ポートを塞いだ状態で `h3.Client.connect(timeout=1.0)` が 1 秒強で `ConnectTimeoutError` を送出するテストを追加して確認する（例: `127.0.0.1:1` のような閉じたポートに対して実行）
- TCP ポートを塞いだ状態で `h2.Client.connect(timeout=1.0)` が 1 秒強で `ConnectTimeoutError` を送出するテストを追加して確認する

## 依存関係

- 本 issue は他の Phase issue と独立して着手可能。「Server API を SessionWriter 型に統一する」「`webtransport.Server`」の完了は待たなくてよい。
- 本 issue の完了は「`webtransport.Client`（transport 明示指定）」issue の前提条件となる（統合 Client は下位 Client の `connect` シグネチャに委譲するため）。

## 参考

- `refs/h3/rfc9114.txt` §3.1 "Connectivity problems (e.g., blocking UDP) can result in a failure to establish a QUIC connection; clients SHOULD attempt to use TCP-based versions of HTTP in this case"
