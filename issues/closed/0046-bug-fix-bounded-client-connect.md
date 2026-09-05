# Client.connect の無制限待機ループを修正して bounded にする

- Created: 2026-08-07
- Completed: 2026-09-05
- Branch: feature/fix-bounded-client-connect
- Polished: 2026-08-26

## 目的

`h3.Client.connect()` および `h2.Client.connect()` の内部待機ループのうち、真に無制限なものが片方向遮断 (UDP blackhole や TCP half-open) 状況下でハンドラを無限にブロックする。また、既に固定回数で bounded なループも定数がユーザーから見えず、設定できない。加えて戻り値が `bool` のため「サーバー拒否」と「ネットワーク的な到達失敗」を区別できず、呼び出し側が後段でリトライや fallback を判定する余地が無い。

本 issue はこの独立バグを修正し、`connect()` が指定タイムアウト内に成功／失敗を必ず返すようにして、失敗理由を具体例外で伝える。

## 現状

### h3.Client.connect の待機ループ

`src/webtransport/h3/client.py` の `Client.connect` 内には次の 3 つの待機ループが存在する。分類は次のとおり。

1. **HANDSHAKE 完了待ちループ (真に無制限)**: `while not handshake_done and self._running:` で回り続ける。ループ内の `_receive` は `asyncio.wait_for` で 0.1 秒タイムアウトを持つが、それを `except TimeoutError: pass` で吸収するため、UDP パケットが 1 つも返ってこない場合はこのループが永久に回る。`self._running` は接続イベント (`CONNECTION_CLOSED`) でしか `False` にならず、外部からのタイムアウト到達手段が無い
2. **SETTINGS 受信待ちループ (bounded だがユーザーから見えない固定値)**: `while not settings_received and self._running and attempt < max_attempts:` で `max_attempts = 100` と `asyncio.sleep(0.01)` の組み合わせにより約 11 秒でループを抜ける。この 11 秒の内訳は、ループ 1 周あたり `_receive` 内の `asyncio.wait_for(..., timeout=0.1)` (最大 0.1 秒) + `asyncio.sleep(0.01)` (0.01 秒) の合計 0.11 秒が支配的で、100 × 0.11 ≒ 11 秒となる。この定数はユーザーから見えず設定できず、下限が固定である
3. **2xx 応答待ちループ (bounded だがユーザーから見えない固定値)**: `for _ in range(100):` で `_receive` (最大 0.1 秒) + `asyncio.sleep(0.01)` を回し、約 11 秒で応答なし (`SESSION_READY` / `SESSION_REJECTED` / `SESSION_CLOSED` のいずれも受信しない) 場合にループを抜ける。この定数もユーザーから見えず設定できない

### h2.Client.connect の待機ループ

`src/webtransport/h2/client.py` の `Client.connect` 内には、2xx 応答待ちループ `while self._running:` が存在する。`asyncio.open_connection` は OS デフォルトのタイムアウトに委ねる (実質無制限)。ループ側もサーバーが EOF / RST を送らずイベントも返さない (完全無応答・half-open 化した TCP) 場合に永久ループとなる。EOF 到達時は `_receive` の `if data:` 分岐で `self._running = False` にするため抜ける。`_wait_webtransport_ready` は `timeout_seconds` 引数を持ち bounded になっているが、その後の 2xx 応答待ちが bounded ではない。

### 戻り値の情報量不足

いずれの `connect()` も戻り値は `bool` で、成功時 `True` / 失敗時 `False` を返す。呼び出し側は失敗理由 (タイムアウト、TCP RST、TLS 失敗、サーバー拒否、Extended CONNECT 拒否など) を区別できない。将来 H3 → H2 の自動 fallback を検討する際にも、失敗理由の区別は判定の前提となる。

`quic.Client.connect(timeout=10.0) -> bool` は既に timeout 引数を持ち、期限までに確立できない場合は接続を維持したまま `False` を返す設計になっている。本 issue はこの `quic.Client.connect` のシグネチャは変更せず、`h3.Client.connect` / `h2.Client.connect` のみを例外送出型に統一する (統合クライアント `webtransport.Client` の設計時に 3 層の統一を再検討する)。

一次資料としては `refs/h3/rfc9114.txt` §3.1 が "Connectivity problems (e.g., blocking UDP) can result in a failure to establish a QUIC connection; clients SHOULD attempt to use TCP-based versions of HTTP in this case" と述べているが、これを実装する前段として、そもそも「QUIC 到達失敗の判定」自体が bounded に完了する必要がある。

## 設計方針

`connect()` を deadline ベースで bounded にし、失敗を具体例外で通知する。

- `h3.Client.connect(timeout: float = 10.0) -> None` および `h2.Client.connect(timeout: float = 10.0) -> None` に統一する。成功時は `None` を返し、失敗時は下記の具体例外を送出する。既存の `bool` 戻り値は廃止する
- deadline は `asyncio.get_running_loop().time() + timeout` で 1 度計算し、`connect()` 内のすべての待機ループがこの同一 deadline を参照する。ループ本体は `while loop.time() < deadline` で判定する
- 例外階層は `src/webtransport/exceptions.py` を新設して以下を定義する。すべて `WebTransportConnectError` を親に持つ
  - `WebTransportConnectError`: すべての `connect()` 失敗例外の基底クラス
  - `ConnectTimeoutError`: 指定 `timeout` を超えても handshake / SETTINGS / 2xx 応答が完了しなかった場合 (待機中に成否を決める具体イベントが 1 つも届かず deadline に達したケース)
  - `ConnectRefusedError`: 待機中に TCP RST、QUIC 側の明示的な `CONNECTION_CLOSE`、または TLS ハンドシェイクの前段での接続拒否が届いた場合。`asyncio.open_connection` の Python 標準 `ConnectionRefusedError` (`OSError` 派生) は wrap して raise し、元例外を `__cause__` に保持する
  - `HandshakeFailedError`: TLS ハンドシェイクの検証失敗 (証明書検証エラー・ALPN 不一致など)、または Extended CONNECT のセマンティック失敗 (非 2xx 応答 = `SESSION_REJECTED` 相当) の場合
- 例外の判定順序ルール:
  1. 待機中に成否を決めるイベント (RST / `CONNECTION_CLOSE` / 非 2xx 応答 / TLS 検証失敗) が届いた場合は `ConnectRefusedError` または `HandshakeFailedError` を優先して送出する
  2. イベントが届かないまま deadline に達した場合のみ `ConnectTimeoutError` を送出する
  3. QUIC ハンドシェイク中の `CONNECTION_CLOSE` (証明書エラー・ALPN 不一致など) は TLS 由来のクローズと判定できるため `HandshakeFailedError` に寄せる。それ以外の `CONNECTION_CLOSE` は `ConnectRefusedError` に寄せる
- `h3.Client` 内では HANDSHAKE 待ち / SETTINGS 待ち / 2xx 応答待ちの 3 つのループをすべて同一 deadline で制御し、SETTINGS 側の `max_attempts = 100` と 2xx 応答側の `range(100)` の固定値はいずれも廃止する
- `h2.Client` 内では 2xx 応答待ちループを deadline 制御に切り替え、`_wait_webtransport_ready` の `timeout_seconds` にも同じ deadline から算出した残り時間を渡す
- `asyncio.open_connection` は `asyncio.wait_for(..., timeout=remaining)` でラップし、接続段階もタイムアウトさせる
- 破壊的変更として、既存の `connect()` の `bool` 戻り値に依存するテスト・examples は例外ハンドリングに書き換える。`tests/test_e2e_webtransport_h3.py` および `tests/test_e2e_webtransport_h2.py` の `assert connected is True` / `is False` パターンは合計 30 箇所以上あり、`connected = await client.connect()` の代入行とあわせて書き換え行は 60 行以上になる。これらすべてを `await client.connect()` (例外なし) / `with pytest.raises(...)` 形式に置き換える。`CHANGES.md` に `shiguredo-changelog` スキルの規約に沿って `[CHANGE]` / `[FIX]` / `[ADD]` の妥当な種別で必要数のエントリを記載する (シグネチャ変更・例外クラス追加・無限ループ修正で複数エントリになる)

## 完了条件

- `h3.Client.connect(timeout=1.0)` を、無応答の UDP 宛先 (例: blackhole IP `10.255.255.1:443` などのルーティングは通るが応答が返らないアドレス) に対して呼ぶと、1 秒強で `ConnectTimeoutError` が送出される。ローカルの閉じたポート (`127.0.0.1:1` など) は環境により ICMP port unreachable で `OSError` / `ConnectRefusedError` が即座に返るため、タイムアウトの検証には不適
- `h2.Client.connect(timeout=1.0)` を、listen しているが accept しない TCP サーバー (テスト内で作成する) に対して呼ぶと、1 秒強で `ConnectTimeoutError` が送出される
- `h3.Client.connect()` と `h2.Client.connect()` の戻り値がなくなり、成功は例外なしの正常復帰、失敗は具体例外による通知に統一される
- `src/webtransport/exceptions.py` に `WebTransportConnectError` / `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` が定義され、`webtransport` トップレベルから import できる
- `h3.Client` の SETTINGS 待ちで使われていた `max_attempts = 100` と 2xx 応答待ちで使われていた `range(100)` の固定値がいずれも削除されている
- `tests/test_e2e_webtransport_h3.py` / `tests/test_e2e_webtransport_h2.py` の `assert connected is True/False` パターンがすべて新シグネチャに追従している (概算 30 箇所以上、代入行を含む書き換え行は 60 行以上)
- `examples/webtransport/h3_client.py` / `examples/webtransport/h2_client.py` が例外ハンドリング形式に書き換えられている

## 解決方法

対象ファイル:

- `src/webtransport/h3/client.py` (`connect` シグネチャ、待機ループの deadline 化、3 ループの統合)
- `src/webtransport/h2/client.py` (`connect` シグネチャ、2xx 応答待ちの deadline 化、`asyncio.open_connection` のタイムアウトラップ、標準 `ConnectionRefusedError` の wrap)
- `src/webtransport/exceptions.py` (新設)
- `src/webtransport/__init__.py` (例外クラスの再エクスポート)
- `examples/webtransport/h3_client.py` (例外ハンドリングに書き換え)
- `examples/webtransport/h2_client.py` (同上)
- `tests/test_e2e_webtransport_h3.py` (成功時は `await client.connect()`、失敗時は `pytest.raises(...)` の形に)
- `tests/test_e2e_webtransport_h2.py` (同上)
- 新規テスト: 既存 E2E ファイルに追加した (shiguredo-python の命名規則に従い `tests/test_client_connect_timeout.py` のような対応モジュールのない名前は避けた)
- `skills/webtransport-py/SKILL.md` (Client 節の `connect` 記述を更新した)
- `CHANGES.md` (`[CHANGE]` シグネチャ変更 / `[ADD]` 例外クラス追加 / `[FIX]` 無限ループ修正の 3 エントリを追加した)

### 実装内容

- `src/webtransport/exceptions.py` を新設し、`WebTransportConnectError` を親とする `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` を定義した。`webtransport` トップレベルから再エクスポートする
- `h3.Client.connect(timeout=10.0) -> None` に変更し、HANDSHAKE 待ち / SETTINGS 待ち / 2xx 応答待ちの 3 ループを同一 deadline で制御した。`max_attempts = 100` と `range(100)` の固定値を廃止し、ハンドシェイク中の `CONNECTION_CLOSE` と transport parameter 未達・非 2xx 応答は `HandshakeFailedError`、それ以外の `CONNECTION_CLOSE` は `ConnectRefusedError`、deadline 到達は `ConnectTimeoutError` とした
- `h2.Client.connect(timeout=10.0) -> None` に変更し、`asyncio.open_connection` を残り時間の `wait_for` でラップした (標準 `ConnectionRefusedError` は wrap して `__cause__` に保持し、TLS 失敗は `HandshakeFailedError` とする)。`_wait_webtransport_ready` にも残り時間を渡し、2xx 応答待ちを deadline 制御にした
- `tests/test_e2e_webtransport_h3.py` / `tests/test_e2e_webtransport_h2.py` の真偽値パターンを例外形式に書き換え、`tests/test_webtransport_h3_error_code_remap.py` の 3 箇所も追従した。新規テストとして blackhole 宛ての `ConnectTimeoutError` (h3)、無応答 TCP の `ConnectTimeoutError` と閉ポートの `ConnectRefusedError` (h2) を追加した。h3 の `ConnectRefusedError` 経路は既存の H3_MESSAGE_ERROR 拒否テストで決定的に検証する (サーバー停止をポーリングで競わせる方式は非決定的のため採用しない)
- `examples/webtransport/h3_client.py` / `h2_client.py` を例外ハンドリング形式にし、`skills/webtransport-py/SKILL.md` の Client 節を更新した
- `CHANGES.md` に `[CHANGE]` / `[ADD]` / `[FIX]` の 3 エントリを追加した

## 検証

- 実装中に拡張モジュールのビルドが C++ ソースより古いことに気づき (`SESSION_REJECTED` 等が欠落)、`make develop` でリビルドした。生成物は追跡対象外のためコミットに含めない

- `uv run pytest tests/` を通す
- `uv run pytest tests/prop_webtransport_h3.py tests/prop_webtransport_h2.py` の Hypothesis プロパティテストがリグレッションしないことを確認する
- 無応答の UDP 宛先 (blackhole IP) に対して `h3.Client.connect(timeout=1.0)` が 1 秒強で `ConnectTimeoutError` を送出するテストを追加して確認する
- listen only の TCP サーバーに対して `h2.Client.connect(timeout=1.0)` が 1 秒強で `ConnectTimeoutError` を送出するテストを追加して確認する
- `ConnectRefusedError` の検証も別ケースで行う (`127.0.0.1:1` のような閉じたポートを使う)

## 依存関係

- 本 issue は他の Phase issue と独立して着手可能。「Server API を SessionWriter 型に統一する」「`webtransport.Server`」の完了は待たなくてよい
- 本 issue の完了は「`webtransport.Client`（transport 明示指定）」issue の前提条件となる (統合 Client は下位 Client の `connect` シグネチャに委譲するため)
- 0122 の項目 3 (h3 の SETTINGS 受信判定を stream_id ハードコードから直接観測へ) が先に着手された場合、本 issue の SETTINGS 待ちループの実装形は `Session` の `SETTINGS_RECEIVED` イベント観測型になる。実装着手時に 0122 の進捗状況を確認し、既に 0122 側で SETTINGS 判定が変更されていれば本 issue の deadline 化はそのイベント観測ループを対象にする

## 参考

- `refs/h3/rfc9114.txt` §3.1 "Connectivity problems (e.g., blocking UDP) can result in a failure to establish a QUIC connection; clients SHOULD attempt to use TCP-based versions of HTTP in this case"

## pending にした理由

h2/h3 統一 listen (`webtransport.Server` dual-listen glue) 関連の実装 (0044-0047) を一旦後回しにすることにしたため、その前提群の一部である本 issue も保留する。実装再開時に reopened にする。

## reopened にした理由

0044-0047 を後回しにする方針は現状も有効だが、本 issue は独立したバグ修正 (h3.Client / h2.Client の `connect()` 内の真に無制限な待機ループを bounded にする) であり、他の Phase issue の完了を待たずに先行実装できる。`connect()` が bounded にならないと、UDP blackhole や TCP half-open (完全無応答) 下でハンドラが無限にブロックする実害があるため、優先して reopen する。
