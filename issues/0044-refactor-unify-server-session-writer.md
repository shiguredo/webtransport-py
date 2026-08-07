# Server API を SessionWriter 型に統一する

- Created: 2026-08-07
- Completed:
- Branch: feature/refactor-unify-server-session-writer
- Polished:

## 目的

`webtransport.h3` と `webtransport.h2` の高レベル Server API は現在、コールバックのシグネチャが根本的に非対称になっている。この非対称のままでは、ひとつのハンドラで H3 / H2 両方のセッションを受け取る統合サーバー（別 issue で扱う `webtransport.Server` dual-listen glue）を実装できない。

本 issue はその前提条件として、`h3.Server` を `h2.Server` と同じ **SessionWriter オブジェクト駆動** の API に揃える。

## 現状

`src/webtransport/h3/server.py` の `Server` はアドレス駆動 API になっており、コールバックは `session_id` と `addr: tuple[str, int]` を受け取り、送信メソッドも `Server.send_stream_data(addr, stream_id, ...)` / `Server.send_datagram(addr, session_id, data)` / `Server.open_stream(addr, session_id, unidirectional)` のように、呼び出し側が接続を特定する `addr` を毎回渡す形になっている。

一方 `src/webtransport/h2/server.py` の `Server` は接続ごとの `SessionWriter` オブジェクトを生成し、コールバックにその `SessionWriter` を渡す形になっている。`SessionWriter` は `send_stream_data(stream_id, data, fin)` / `send_datagram(data)` / `open_stream(unidirectional)` / `reset_stream(stream_id, error_code)` / `close_session(error_code, error_message)` と、セッションを既に閉じ込めた状態のメソッドを提供する。

この差により、同じハンドラ関数を両サーバーに渡すことができない。加えて次の非対称もある。

- `h3.Server.__init__` は `allowed_origins` を受け取るが、`h2.Server.__init__` は受け取らない（引数は `host, port, certfile, keyfile` のみ）
- `h2.SessionWriter` に `remote_addr` に相当するプロパティが無く、H2 側では接続元アドレスをハンドラから取得できない
- `_parse_url` が `src/webtransport/h3/client.py` と `src/webtransport/h2/client.py` に同一実装で重複している

一次資料の位置付けとしては `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 が「endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection」、`refs/webtrans/draft-ietf-webtrans-http2-15.txt` Appendix A が「endpoints SHOULD prefer [version-specific WebTransport protocol] over the capsule-based protocol」と述べており、両プロトコルを同一サーバーが並行サポートすることを想定している。API 非対称の解消は、その並行サポートを Python 側で提供するための前段。

## 設計方針

`h2.Server` の `SessionWriter` を「両サーバー共通の抽象」の位置付けに引き上げ、`h3.Server` にも同型の `H3SessionWriter` を導入する。ハンドラは常に `SessionWriter` プロトコルを満たす writer オブジェクトを 1 つだけ受け取る形にする。

- `SessionWriter` プロトコル（`typing.Protocol`）は `src/webtransport/_common.py`（新設）に定義し、`send_stream_data` / `send_datagram` / `open_stream` / `reset_stream` / `close_session` の各 async メソッドと、`session_id` / `remote_addr` / `transport`（`"h3"` または `"h2"`）プロパティを含む。
- `h3.Server` にはコネクション単位の `H3SessionWriter` クラスを新設し、`_clients` から該当エントリを引く責務を Writer 側に閉じ込める。既存の `Server.send_stream_data(addr, stream_id, ...)` / `Server.send_datagram(addr, session_id, data)` / `Server.open_stream(addr, session_id, unidirectional)` は廃止する。
- `on_session_ready` / `on_session_closed` / `on_stream_data` / `on_stream_reset` / `on_datagram` のコールバック署名を両サーバーで一致させる。`addr` は Writer の `remote_addr` プロパティから参照する形に統一する。
- `h2.SessionWriter` に `remote_addr` プロパティを追加する（`asyncio.StreamWriter.get_extra_info("peername")` から取得）。`transport` プロパティも追加し、常に `"h2"` を返す。`H3SessionWriter` の `transport` は常に `"h3"` を返す。
- `h2.Server.__init__` に `allowed_origins: list[str] | None = None` を追加する。H3 と同じ origin 検証セマンティクス（`None` と空リストはどちらも全オリジンを受理）にする。
- `src/webtransport/h3/client.py` と `src/webtransport/h2/client.py` に重複する `_parse_url` を `src/webtransport/_common.py` の `parse_wt_url` に集約する（Client 側からもこの関数を呼ぶだけ）。

破壊的変更として、既存の `h3.Server` を使うユーザーはコールバック署名と送信メソッド呼び出しをすべて書き換える必要がある。`CHANGES.md` に `shiguredo-changelog` スキルの規約に沿って `[CHANGE]` エントリを記載する。

## 完了条件

- `h3.Server` と `h2.Server` の 5 種のコールバック（`on_session_ready`, `on_session_closed`, `on_stream_data`, `on_stream_reset`, `on_datagram`）が完全に同一の署名を持ち、それぞれ Writer オブジェクトを受け取る
- `h3.Server` / `h2.Server` の Writer が `src/webtransport/_common.py` の `SessionWriter` プロトコルを満たす（`typing.get_type_hints` や `isinstance` チェックで確認できる）
- `h2.Server.__init__` が `allowed_origins` を受け取り、origin 検証セマンティクスが `h3.Server` と一致する
- `_parse_url` が `src/webtransport/_common.py` に集約され、`h3.Client` / `h2.Client` の重複コードが削除されている
- `CHANGES.md` に破壊的変更エントリが追加されている

## 解決方法

対象ファイル:

- `src/webtransport/h3/server.py`（改修）
- `src/webtransport/h2/server.py`（`SessionWriter` 拡張、`allowed_origins` 追加）
- `src/webtransport/_common.py`（新設。`SessionWriter` Protocol と `parse_wt_url`）
- `src/webtransport/h3/client.py`（`_parse_url` 削除、`_common.parse_wt_url` を利用）
- `src/webtransport/h2/client.py`（同上）
- `examples/webtransport/h3_server.py`（新 API 反映）
- `examples/webtransport/h2_server.py`（新 API 反映）
- `tests/test_e2e_webtransport_h3.py`（署名変更に伴う修正）
- `tests/test_e2e_webtransport_h2.py`（同上）
- `skills/webtransport-py/SKILL.md`（Server 節の API 記述更新）
- `CHANGES.md`（`shiguredo-changelog` スキルに従って `[CHANGE]` エントリを追加）

## 検証

- `uv run pytest tests/` が全通することを確認する（`test_e2e_webtransport_h3.py` / `test_e2e_webtransport_h2.py` の新署名での動作確認を含む）
- `uv run pytest tests/prop_webtransport_h3.py tests/prop_webtransport_h2.py` の Hypothesis プロパティテストがリグレッションしないことを確認する
- `examples/webtransport/h3_server.py` と `examples/webtransport/h2_server.py` を新 API で動かして、それぞれ対応する `h3_client.py` / `h2_client.py` から接続・データグラム送受信・ストリーム送受信ができることを手動で確認する

## 依存関係

- 本 issue は独立して着手可能。
- 本 issue の完了は、以下の後続 issue の前提条件となる。
  - `webtransport.Server`（dual-listen glue）の追加
  - `webtransport.Client`（transport 明示指定）の追加

## 参考

- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` §2.1.2 "endpoints SHOULD prefer this protocol when using WebTransport over an HTTP/3 connection"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` §1 "the current most common TCP-based fallback to HTTP/3"
- `refs/webtrans/draft-ietf-webtrans-http2-15.txt` Appendix A "endpoints SHOULD prefer [version-specific WebTransport protocol] over the capsule-based protocol"
