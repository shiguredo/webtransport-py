# 未カバーの失敗経路のテストを追加する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/test-add-missing-failure-path-tests
- Polished: 2026-09-15

## 目的

正常系は広くカバーされているが、公開 API の失敗経路にテストが無い箇所がある。失敗時の契約がテストで固定されていないと、実装の変更で黙って壊れる。

## 現状

http3 層の connect:

- `src/webtransport/http3/client.py` の `Client.connect` が送出する例外は `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` の 3 種である。原因ごとの送出点は、名前解決失敗 (同 390-393 行目) と接続生成失敗 (同 468-474 行目) が `ConnectRefusedError`、ハンドシェイク完了前の CONNECTION_CLOSED (同 493-495 行目) が `HandshakeFailedError`、期限到達が `ConnectTimeoutError` (同 372 / 395 / 506 / 516 行目) である
- このうち期限到達は `tests/test_connect_loss_recovery.py` の `test_http3_connect_blackhole_timeout` が、名前解決失敗は同 `test_http3_connect_refused_invalid_host` が検証している。残る未カバーは `HandshakeFailedError` と接続生成失敗の 2 経路である
- 「候補 0 件」(同 394-395 行目) は `_resolve_remote` が空リストを返す場合に限られ、`getaddrinfo` が空リストを返す入力が無いためモック無しでは到達できない。本 issue の対象外とする
- `tests/test_e2e_http3.py` に `pytest.raises` は 1 件も無い。ただし同ファイルは正常系の往復を扱うため、失敗経路の有無はこの件数では判定できない

h3 層の connect:

- `src/webtransport/h3/client.py` の `Client.connect` は、ハンドシェイク完了後に対向が WebTransport 対応の SETTINGS を送らないまま期限に達すると `ConnectTimeoutError` を送出する (同 572-575 行目)。この経路を検証するテストが無い。既存の h3 の connect 失敗テストは、ハンドシェイクの期限到達 (`tests/test_e2e_webtransport_h3.py` の `test_connect_timeout_on_blackhole`) とトランスポートパラメータ欠落・Origin 拒否を扱うのみである
- 同 `Client.connect` は、CONNECT を送出した後に 2xx も非 2xx も届かないまま期限に達すると `ConnectTimeoutError` を送出する (同 677 行目)。`h3.Server` は `on_session_request` の戻り値で受理か拒否を決めるため (519-546 行目)、このコールバックが期限まで戻らなければこの経路に到達する (実測)。この経路を検証するテストも無い

h2 層の connect:

- 接続拒否・TLS アラート・期限到達・非 2xx は既存テストでカバーされている。非 2xx は `tests/test_e2e_webtransport_h2.py` の `test_h2_client_connect_raises_on_non_2xx_reject`、期限到達は同 `test_connect_timeout_on_listen_only_server`、接続拒否は同 `test_connect_refused_on_closed_port`、TLS アラートは `tests/test_webtransport_h2_tls_version.py` の `test_h2_client_rejects_tls_1_2_only_server` である。h2 層には追加しない

並行アクセス:

- `tests/test_quic_free_threading.py` は `quic.Connection` / `h3.Session` / `http3.Connection` / `h2.Session` / `http2.Connection` の 5 種類を実スレッドで叩くが、`Config` は各テストがワーカー起動前に生成して設定するだけで、同一の `Config` インスタンスを複数スレッドから触るテストが無い。`CODEBASE.md` は `quic.Config.verify_callback` を唯一の Python コールバック境界として挙げており、`Config` は共有され得る可変オブジェクトである
- ハンマーは 2 スレッドで終了時刻を共有するだけで開始タイミングを揃えておらず、`join()` にタイムアウトが無い。`pyproject.toml` の addopts に timeout が無いため、ローカルの `pytest` 実行ではハングし得る

その他:

- `quic.Packet` の `local_host` / `local_port` を参照するテストが無い。`remote_host` / `remote_port` は高レベル層が使い e2e で踏まれるが、local 側はどこからも参照されない
- Sans-IO の `quic.Connection.initiate_migration` を直接呼ぶテストが無く、高レベル `Client.migrate()` 経由でのみ実行される

## 設計方針

- 失敗経路のテストは既存の E2E の構成を流用し、実サーバーを相手に例外の型と発生を確認する
- モックやスタブは使わない (`AGENTS.md`)。実ソケットと実サーバーで構成する
- 到達手段は実測で確認したものを使う。http3 の `HandshakeFailedError` は自己署名証明書の実サーバーに `verify_peer=True` で接続して証明書検証を失敗させる (ALPN 不一致では期限到達になり到達しない)。http3 の接続生成失敗は存在しない `ca_file` を渡す (TLS コンテキストの生成に失敗し `ConnectRefusedError` になる)。h3 の SETTINGS 待ちの期限到達は、ハンドシェイクのみで WebTransport 対応 SETTINGS を送らない `quic.Server` を使う。2xx 応答待ちの期限到達は、`on_session_request` が期限まで戻らない `h3.Server` を使う
- 追加先は connect() の失敗経路テストが既にある `tests/test_connect_loss_recovery.py` に寄せる。同ファイルの docstring はロス回復のみを掲げているため、実態に合わせて更新する
- 並行アクセスのテストは、2 スレッドの開始タイミングを `threading.Barrier` で揃えたうえで `Config` を共有するハンマーを追加する
- ハンマーの `join` にはタイムアウトを渡し、復帰後にワーカーが残っていないことを表明する
- `local_host` / `local_port` は、Sans-IO の `Connection.create_client` に渡したアドレスと最初の `send()` が返す `Packet` の値が一致することを検証する。2 回目以降の `send()` は pacing の期限待ちで `None` を返し得るため使わない
- Sans-IO の `initiate_migration` は、ハンドシェイクの確認後に呼び、戻り値とその後に送出される `Packet` のローカルアドレスを検証する

## 完了条件

- http3 の `Client.connect` の `HandshakeFailedError` (ハンドシェイク完了前の CONNECTION_CLOSED) を検証するテストがある
- http3 の `Client.connect` の接続生成失敗 (`ConnectRefusedError`) を検証するテストがある
- h3 の `Client.connect` の SETTINGS 待ちの期限到達と、2xx 応答待ちの期限到達を検証するテストがある
- 2 スレッドが `threading.Barrier` で開始タイミングを揃え、同一の `Config` を共有するハンマーがある
- ハンマーの `join` にタイムアウトが設定され、満了時にワーカーが残っていれば失敗する
- `quic.Packet` の `local_host` / `local_port` が、Sans-IO の `create_client` に渡したローカルアドレスと一致することを検証するテストがある
- Sans-IO の `initiate_migration` を直接呼ぶテストがあり、戻り値が True であることと、以後の `send()` が返す `Packet` のうち少なくとも 1 つが新しいローカルアドレスを持つことを検証している
- 追加した各テストは、対象の分岐を潰すと失敗することを確認している
- `uv run pytest tests/ --timeout=30` が通過する

## 解決方法

- `tests/test_connect_loss_recovery.py` に http3 の `HandshakeFailedError` のテストを追加する。自己署名証明書の実サーバー (`quic.Server`) に対し、`verify_peer=True` の `http3.Client` で接続し、`pytest.raises(HandshakeFailedError)` と `is_connected is False` を検証する
- `tests/test_connect_loss_recovery.py` に http3 の接続生成失敗のテストを追加する。存在しない `ca_file` を渡した `verify_peer=True` の `http3.Client` で `connect()` し、`pytest.raises(ConnectRefusedError)` を検証する。生成段階で失敗するためサーバーは要らない
- `tests/test_connect_loss_recovery.py` に h3 の SETTINGS 待ちの期限到達のテストを追加する。WebTransport 対応 SETTINGS を送らない `quic.Server` に対し、`h3.Client` で `connect(timeout=...)` して `pytest.raises(ConnectTimeoutError)` を検証する
- `tests/test_connect_loss_recovery.py` に h3 の 2xx 応答待ちの期限到達のテストを追加する。`on_session_request` が期限まで戻らない `h3.Server` (未設定の `asyncio.Event` を待つ等) に対し、`h3.Client` で `connect(timeout=...)` して `pytest.raises(ConnectTimeoutError)` を検証する。コールバックはテスト終了時に cancel する
- `tests/test_connect_loss_recovery.py` の docstring を、connect() の失敗経路とロス回復の両方を扱う記述に直す
- `tests/test_quic_free_threading.py` の `_hammer` に `threading.Barrier` を導入して 2 スレッドの開始タイミングを揃え、`join(timeout=duration + 5.0)` と、復帰後に `is_alive()` が false であることの表明を追加する
- `tests/test_quic_free_threading.py` に `quic.Config` を共有するハンマーを追加する。1 つの `quic.Config` を 2 スレッドで共有し、片方が `alpn_protocols` を書き換え、もう片方が `alpn_protocols` と `max_data` を読む。表明は「例外が出ない」かつ「読み出した `alpn_protocols` が書き込み値のいずれかと等しい」とする
- `tests/test_quic.py` に `quic.Packet` の検証を追加する。`CLIENT_ADDR` / `SERVER_ADDR` で `quic.Connection.create_client` を作り、最初の `send()` が返す `Packet` について、`local_host` / `local_port` が `CLIENT_ADDR`、`remote_host` / `remote_port` が `SERVER_ADDR` と一致することを表明する。2 回目以降の `send()` は pacing の期限待ちで `None` を返し得るため使わない
- `tests/test_quic.py` に Sans-IO の `initiate_migration` テストを追加する。`perform_handshake` の直後は `initiate_migration` が False を返すため (実測)、追加の往復を重ねてから `initiate_migration(新しいローカルアドレス, SERVER_ADDR)` を呼ぶ (実測では往復 1〜2 回で True)。戻り値が True であること、および以後の `send()` が返す `Packet` のうち少なくとも 1 つが新しいローカルアドレスを持つことを表明する。移行後もクライアントは旧アドレスと新アドレスの両方で送信するため「少なくとも 1 つ」で判定し、`send()` が `None` を返す場合は `wait_pacing_timeout` で期限まで待って再試行する
- `tests/test_quic_free_threading.py` は別 issue (0227) も変更するため、競合した場合はリベースする
