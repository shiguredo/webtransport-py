# 未カバーの失敗経路のテストを追加する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/test-add-missing-failure-path-tests
- Polished: {YYYY-MM-DD}

## 目的

正常系は広くカバーされているが、公開 API の失敗経路にテストが無い箇所がある。失敗時の契約がテストで固定されていないと、実装の変更で黙って壊れる。

## 現状

http3 層の connect:

- `tests/test_e2e_http3.py` に `pytest.raises` が 1 件も無い
- `src/webtransport/http3/client.py` の `Client.connect` が送出する `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` の各経路 (候補 0 件、接続生成失敗、ハンドシェイク中の CONNECTION_CLOSED、SETTINGS 待ちの期限到達、2xx 応答の期限到達) を検証するテストが無い
- これらの例外を参照しているテストは `tests/test_e2e_webtransport_h2.py` / `test_e2e_webtransport_h3.py` / `test_connect_loss_recovery.py` / `test_webtransport_h2_tls_version.py` のみである

h2 層の connect:

- `src/webtransport/h2/client.py` の `Client.connect` の失敗挙動 (接続拒否、TLS アラート、期限到達、非 2xx) のうち、非 2xx 以外を検証するテストが無い

並行アクセス:

- `tests/test_quic_free_threading.py` は `quic.Connection` / `h3.Session` / `http3.Connection` / `h2.Session` / `http2.Connection` の 5 種類を実スレッドで叩くが、各層の `Config` は 1 度も登場しない。`CODEBASE.md` は `quic.Config.verify_callback` を唯一の Python コールバック境界として挙げており、`Config` は共有され得る可変オブジェクトである
- ハンマーは 2 スレッドで開始時刻を共有するだけで同時進入を保証しておらず、`join()` にタイムアウトが無い。`pyproject.toml` の addopts に timeout が無いため、ローカルの `pytest` 実行ではハングし得る

その他:

- `quic.Packet` の `local_host` / `local_port` を参照するテストが無い。`remote_host` / `remote_port` は高レベル層が使い e2e で踏まれるが、local 側はどこからも参照されない
- Sans-IO の `quic.Connection.initiate_migration` を直接呼ぶテストが無く、高レベル `Client.migrate()` 経由でのみ実行される

## 設計方針

- 失敗経路のテストは既存の E2E の構成を流用し、到達不能ポート・無応答・非対応プロトコルのサーバーを相手に例外の型と発生を確認する
- モックやスタブは使わない (`AGENTS.md`)。実ソケットと実サーバーで構成する
- 並行アクセスのテストは `threading.Barrier` で同時進入を強制し、`join` にタイムアウトを付ける。`Config` を共有するハンマーを追加する
- `local_host` / `local_port` は、値を検証するテストを追加するか、用途が無いなら公開 API から外す判断を別途行う。本 issue では値を検証するテストを追加する

## 完了条件

- http3 の `Client.connect` の失敗 3 分岐と、h2 の `Client.connect` の失敗経路にテストがある
- `Config` を共有する並行アクセスのテストがあり、`join` にタイムアウトが設定されている
- `quic.Packet` の `local_host` / `local_port` と `quic.Connection.initiate_migration` にテストがある
- 全テストが通過する

## 解決方法

- `tests/test_e2e_http3.py` に `Client.connect` の失敗経路のテストを追加する (`tests/test_webtransport_h2_tls_version.py` の構成を参考にする)
- `tests/test_e2e_webtransport_h2.py` に `Client.connect` の失敗経路のテストを追加する
- `tests/test_quic_free_threading.py` に `Config` 共有のハンマーを追加し、`threading.Barrier` と `join(timeout=...)` を導入する
- `tests/test_quic.py` に `quic.Packet` の `local_host` / `local_port` の検証を追加する
- `tests/test_quic.py` または `tests/test_e2e_quic_advanced.py` に Sans-IO の `initiate_migration` を直接呼ぶテストを追加する
