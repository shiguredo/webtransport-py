# 高レベル API に鍵更新 (initiate_key_update) を露出する

- Created: 2026-08-09
- Completed: YYYY-MM-DD
- Branch: feature/add-key-update-high-level-api
- Polished: {YYYY-MM-DD}
- Reporter: @voluntas

## 目的

Sans I/O API に存在する `QuicConnection.initiate_key_update()` を、asyncio ベースの高レベル API (quic / h3 / http3 の Client / Server) からも呼び出せるようにする。現状は高レベル API の利用者が鍵更新を開始したい場合、`Client._quic_connection` などの private 属性にアクセスするか、Sans I/O API に直接切り替える必要がある。RFC 9001 Section 6 で規定される鍵更新は、実運用アプリケーション (メディアリレーサーバー等) が定期実行することが想定されるため、高レベル API 単体で完結できるようにする。

## 現状

- Sans I/O API: `src/bindings/quic.cpp` の `QuicConnection::initiate_key_update()` が実装済みで、`src/bindings/quic.cpp` の nanobind バインディングで `initiate_key_update` として公開されている。ngtcp2 内部の assert (state == NGTCP2_CS_POST_HANDSHAKE) を避けるため、ハンドシェイク完了フラグとクライアント側の post-handshake write 完了フラグでガードしている
- 高レベル API: `src/webtransport/quic/client.py` の `Client`、`src/webtransport/quic/server.py` の `Server`、`src/webtransport/h3/client.py` の `Client`、`src/webtransport/h3/server.py` の `Server`、`src/webtransport/http3/client.py` の `Client`、`src/webtransport/http3/server.py` の `Server` のいずれも `initiate_key_update` メソッドを持たない (grep で 0 件)
- テスト: `tests/test_quic_stream_control.py` の `test_initiate_key_update_before_handshake` / `test_initiate_key_update_after_handshake` は Sans I/O API を直接叩く構成 (create_client_server_pair + exchange_packets) であり、高レベル API 経由の鍵更新シナリオはカバーされていない

## 設計方針

- 各層の Client / Server に `initiate_key_update` を追加する。委譲先は各層が保持する `QuicConnection` (Sans I/O) の `initiate_key_update()`
  - `webtransport.quic.Client.initiate_key_update() -> bool`: `self._connection.initiate_key_update()` を返す。`_connection` が None なら False を返す
  - `webtransport.quic.Server.initiate_key_update(addr: tuple[str, int]) -> bool`: `self._connections.get(addr)` の Connection に委譲。addr が未登録なら False を返す (既存の `send_stream_data(addr, ...)` などと同じ addr ベースの API パターン)
  - `webtransport.h3.Client.initiate_key_update() -> bool`: `self._quic_connection.initiate_key_update()` を返す。`_quic_connection` が None なら False を返す
  - `webtransport.h3.Server.initiate_key_update(addr: tuple[str, int]) -> bool`: `self._clients.get(addr).quic_connection.initiate_key_update()` を返す。addr が未登録または quic_connection が None なら False を返す
  - `webtransport.http3.Client.initiate_key_update() -> bool`: `self._quic_connection.initiate_key_update()` を返す。`_quic_connection` が None なら False を返す
  - `webtransport.http3.Server.initiate_key_update(addr: tuple[str, int]) -> bool`: `self._clients.get(addr).quic_connection.initiate_key_update()` を返す
- 戻り値は Sans I/O API と同じ `bool` (成功時 True、ngtcp2 内部ガードまたは HANDSHAKE_CONFIRMED 未成立で False)。呼び出し側の判断材料になるため、無視せず伝搬する
- 非同期メソッドではなく同期メソッドとする (Sans I/O API 自体が同期。ソケット I/O は呼び出し元が別途 `_send_pending()` に相当する内部処理をトリガーする既存パターンに合わせる。ngtcp2 内部で新しい鍵素材を導出するだけであり、実際の KEY_UPHASE の反映はその後の送信パケットで行われる)
- webtransport-py の Sans I/O 側の同名メソッドの docstring を参考に、高レベル側の docstring にも「ハンドシェイク完了前 / HANDSHAKE_CONFIRMED 未成立では False が返る」ことと「連続呼び出しは 2 回目が False になる (RFC 9001 Section 6.1 の MUST に基づく)」ことを明記する
- 変更対象は上記 6 ファイル (src/webtransport/quic/client.py, src/webtransport/quic/server.py, src/webtransport/h3/client.py, src/webtransport/h3/server.py, src/webtransport/http3/client.py, src/webtransport/http3/server.py) と、高レベル API 経由での鍵更新をカバーするテスト (tests/test_e2e_quic.py に quic.Client / quic.Server、tests/test_e2e_http3.py に http3.Client / http3.Server、tests/test_e2e_webtransport_h3.py に h3.Client / h3.Server の各 1 本)
- skills/webtransport-py/SKILL.md の高レベル API リファレンスに `initiate_key_update` を追記する (対応する言い回しの雛形は既存の `keep_alive_timeout` などの記述に合わせる)
- Sans I/O 側のバインディング (`src/bindings/quic.cpp` の `initiate_key_update`) や既存のガードロジックは変更しない (本 issue は高レベル露出のみ)

## 完了条件

- quic / h3 / http3 の Client / Server に `initiate_key_update` メソッドが追加され、それぞれが対応する `QuicConnection.initiate_key_update()` に委譲する
- 高レベル Client / Server 経由でハンドシェイク完了後に `initiate_key_update()` を呼ぶと True が返り、直後の連続呼び出し (鍵更新確認前) では False が返るテストが quic / h3 / http3 の 3 層で通ることを確認する
- 高レベル Client / Server 経由でハンドシェイク完了前に `initiate_key_update()` を呼ぶと False が返るテストが quic / h3 / http3 の 3 層で通ることを確認する
- 鍵更新後にストリームデータ送信 (受信) が継続できることを検証するテストが少なくとも quic 層に 1 本存在する (鍵更新自体が壊れていないことの確認。h3 / http3 は quic 層に依存するため quic 層で担保する)
- skills/webtransport-py/SKILL.md に `initiate_key_update` の記述が追加される
- 既存の全テストが通る

## 解決方法

(実装時に追記する)
