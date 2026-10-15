# 統一 API の Client に on_stop_sending と stop_sending が無い

- Created: 2026-10-15
- Completed: 2026-10-15
- Branch: feature/add-unified-client-stop-sending
- Reporter: @voluntas

## 目的

統一 API (`webtransport.Client`) に `on_stop_sending` と `stop_sending` を追加し、HTTP/2 と HTTP/3 のどちらでもピアの送信停止要求を観測でき、ピアへ送信停止を要求できるようにする。

## 現状

- `src/webtransport/client.py` の `Client` は `send_datagram` / `reset_stream` / `on_stream_reset` を持つが、`on_stop_sending` と `stop_sending` を持たない
- 下位層には揃っている。`src/webtransport/_h3_client.py` の `Client.on_stop_sending` / `Client.stop_sending` と、`src/webtransport/_h2_client.py` の `Client.on_stop_sending` / `Client.stop_sending` はいずれも実装済みである。h3 側の `on_stop_sending` は WebTransport のデータストリームのみを対象とし、エラーコードはレンジ外のとき None になり得る (draft-ietf-webtrans-http3-16 Section 4.4)。h2 側は常に int である
- 統一 `Server.on_stop_sending` は HTTP/2 と HTTP/3 の両方に対応済みである。統一 `Session.stop_sending` も HTTP/2 / HTTP/3 の両方で使える
- 影響: 統一 API の利用者 (moqt-py) は `client.h3` / `client.h2` ハンドルへ降りて `on_stop_sending` を登録し、`h3.Client.stop_sending` / `h2.Client.stop_sending` を直接呼ぶ回避を強いられている。同じ統一 API に `reset_stream` があるのに `stop_sending` が無いという非対称も残っている
- `skills/webtransport-py/SKILL.md` の統一クライアントの節は、共通 API に `stop_sending` を載せず `client.h3` / `client.h2` の表に載せている。`tests/test_unified_api_e2e.py` の `test_stop_sending_over_unified_api` も、ピア側の停止要求をプロトコル固有ハンドルから送っている

## 設計方針

- `Client.on_stop_sending(callback)` は `self._impl().on_stop_sending(callback)` へ委譲する。コールバックの型は統一 `Client.on_stream_reset` と統一 `Server.on_stop_sending` に揃えて `Callable[[int, int | None], Awaitable[None]]` とし、HTTP/3 ではデータストリームのエラーコードがレンジ外のとき None になり得る (HTTP/2 は常に int) ことを docstring に書く
- `Client.stop_sending(stream_id, error_code=0)` は `self._impl().stop_sending(stream_id, error_code)` へ委譲する。既定値とエラーコードの意味は統一 `Session.stop_sending` と h2 / h3 の実装に揃える
- モジュール docstring の「プロトコル固有の API」の列挙から `stop_sending` を外す (`migrate` / `close_stream` / `on_error` は h3 / h2 固有のまま)
- `skills/webtransport-py/SKILL.md` の統一クライアントの節を更新する (共通 API に `stop_sending` と `on_stop_sending` を加え、`client.h3` / `client.h2` の表から `stop_sending` を外す)
- テストは `tests/test_unified_api_e2e.py` の `http_version` による parametrize で HTTP/2 と HTTP/3 の両方を回す。DUT とピアの両方に統一 API を使い、(a) 統一 `Client.stop_sending` が統一 `Server.on_stop_sending` で観測されること、(b) サーバーの `Session.stop_sending` が統一 `Client.on_stop_sending` で観測されることを実 QUIC / 実 TCP で検証する。既存の `test_stop_sending_over_unified_api` のピア側呼び出しも統一 API へ置き換える
- `tests/test_unified_client.py` には、`on_stop_sending` の登録が h2 / h3 の実装へ委譲されることを確認するテストを加える

## 完了条件

- HTTP/2 と HTTP/3 のどちらでも、統一 `Client` の `on_stop_sending` が登録でき、`stop_sending` が送出できる
- `client.stop_sending(stream_id, error_code)` のエラーコードがピアで同じ値として観測される
- ピアの STOP_SENDING が `client.on_stop_sending` で `(stream_id, error_code)` として 1 回だけ通知される
- 配線 (実装への委譲) を外すと追加したテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- h2 層 / h3 層の `on_stop_sending` / `stop_sending` 自体の挙動
- 統一 `Client` の `on_error` と、HTTP/3 固有の `migrate` / `close_stream` などプロトコル固有 API の統一
- QMux 層の `stop_sending` (統一 API の対象ではない)

## 解決方法

- `src/webtransport/client.py` の `Client` に `stop_sending(stream_id, error_code=0)` と `on_stop_sending(callback)` を追加し、`self._impl()` 経由で選択した HTTP バージョンの実装へ委譲した。コールバックの型は統一 `Client.on_stream_reset` と統一 `Server.on_stop_sending` に揃えて `Callable[[int, int | None], Awaitable[None]]` とし、HTTP/3 ではデータストリームのエラーコードがレンジ外のとき None になり得る (HTTP/2 は常に int) ことと、HTTP/3 では WebTransport のデータストリームだけが対象であることを docstring に書いた
- モジュール docstring の「プロトコル固有の API」の列挙から `stop_sending` を外した (`migrate` / `close_stream` / `on_error` は h3 / h2 固有のまま)
- `tests/test_unified_api_e2e.py` の `test_stop_sending_over_unified_api` のピア側呼び出しをプロトコル固有ハンドルから統一 `Client.stop_sending` へ置き換え、逆向きの `test_on_stop_sending_over_unified_api` を追加した。どちらも `http_version` の parametrize で HTTP/2 と HTTP/3 を回し、実 QUIC / 実 TCP で `(stream_id, error_code)` が 1 回だけ通知されることを表明する
- `tests/test_unified_client.py` に `test_on_stop_sending_registration_delegates` (h2 / h3 の実装へ委譲されること) と `test_stop_sending_before_connect_does_not_raise` (セッション未確立でも安全に呼べること) を追加した
- RED は実装前に追加テスト 8 件が `AttributeError` で失敗することを実測した (`AttributeError: 'Client' object has no attribute 'on_stop_sending'` / `'stop_sending'`)。既存テストは通過したままである
- `skills/webtransport-py/SKILL.md` の統一クライアントの節を更新した (共通 API に `stop_sending` と `on_stop_sending` を追加し、`client.h3` / `client.h2` の表から `stop_sending` を削除)
- 対象テストは 24 passed、全テストは 1495 passed。prek の全フックも通過する
