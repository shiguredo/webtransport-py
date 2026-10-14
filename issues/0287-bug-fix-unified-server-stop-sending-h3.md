# 統一 API の Server.on_stop_sending が HTTP/2 専用で HTTP/3 では ValueError になる

- Created: 2026-10-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-unified-server-stop-sending-h3
- Reporter: @voluntas

## 目的

統一 API (`webtransport.Server`) の `on_stop_sending` は HTTP/2 専用であり、HTTP/3 を選ぶと登録時に `ValueError` になる。h3 層は 0251 でピアの STOP_SENDING を nghttp3 へ転送し `h3.Server.on_stop_sending` でアプリへ通知できるようになったため、統一 API だけが非対称に取り残されている。統一 API の利用者は HTTP/3 でピアの送信停止要求を観測できず、`server.h3` へ降りて登録する以外に手段がない (moqt-py はこの回避策を採っている)。`Session.stop_sending` (要求の送出) は HTTP/2 / HTTP/3 の両方で使えるため、統一 API の送信側と受信側が食い違っている。

## 現状

- `src/webtransport/server.py` の `Server.on_stop_sending` は `self._h2` が None のとき `ValueError` を送出し、`self._h2.on_stop_sending` にしか配線していない。`on_stream_reset` / `on_stream_data` / `on_datagram` は `self._h3` / `self._h2` の両方へ配線済みである
- h3 層は `_h3_server.Server.on_stop_sending(callback)` を持ち、コールバックの形は `(session_id, stream_id, error_code, addr)` である。`error_code` はデータストリームでは WT_APPLICATION_ERROR レンジのワイヤコードを 32 bit アプリコードへ逆変換した値で、レンジ外・予約済みコードポイントでは None になる (draft-ietf-webtrans-http3-16 Section 4.4。`on_stream_reset` と同じ規則)
- h2 層は `_h2_server.Server.on_stop_sending(callback)` を持ち、コールバックの形は `(stream_id, error_code, session_writer)` である。HTTP/2 のエラーコードは常に int になる
- 統一 API の `on_stream_reset` は `Callable[[Session, int, int | None], Awaitable[None]]` を受けて両プロトコルへ配線しており、HTTP/3 の None をそのまま渡す形になっている。`on_stop_sending` も同じ形にできる
- `Server.on_error` は HTTP/2 専用のままである (h3 層に `on_error` が無いため。本 issue の対象外)
- `skills/webtransport-py/SKILL.md` の統一サーバーの節は `on_stop_sending` を「HTTP/2 のみ。HTTP/3 は `server.h3` の `on_stop_sending(session_id, stream_id, error_code, addr)` を使う」と案内している
- `tests/test_unified_api_e2e.py` は `http_version` の切り替えだけで両プロトコルを回す統一 API の e2e を持つが、STOP_SENDING の通知は対象になっていない

## 設計方針

- `Server.on_stop_sending` を `on_stream_reset` と同じ形にし、`self._h3` があれば `h3_server.on_stop_sending` へ、`self._h2` があれば `self._h2.on_stop_sending` へ配線する。`ValueError` は削除する (両プロトコルで使えるため)
- コールバックの型は `Callable[[Session, int, int | None], Awaitable[None]]` にする (HTTP/3 のエラーコードはレンジ外のとき None になり得る。HTTP/2 は常に int)。`on_stream_reset` と同じ規則を docstring に書く
- h3 側のハンドルは `on_stream_reset` / `on_stream_data` と同じく `_H3Session(h3_server, addr, session_id)` を渡す。h2 側は既存どおり `_H2Session(writer)` を渡す
- docstring と `skills/webtransport-py/SKILL.md` の統一サーバーのコールバック一覧を更新する
- テストは `tests/test_unified_api_e2e.py` に、`http_version` の切り替えだけで両プロトコルでピアの STOP_SENDING が通知されるシナリオを追加する。DUT は統一 `Server`、ピアは統一 `Client` とし、ピアはプロトコル固有ハンドルの公開 API (`client.h3.stop_sending` / `client.h2.stop_sending`) で停止を要求する。モックやスタブは使わず、実 QUIC / 実 TCP で検証する
- 検証では `Session` ハンドルの `http_version` / `session_id` / `addr` と、`(stream_id, error_code)` の通知内容、通知が 1 回であることを表明する

## 完了条件

- HTTP/3 を選んだ統一 `Server` で `on_stop_sending` を登録しても `ValueError` にならない
- ピアが STOP_SENDING を送ると、統一 `Server` の `on_stop_sending` が `(session, stream_id, error_code)` で 1 回呼ばれる (HTTP/2 / HTTP/3 の両方)
- `session` がプロトコルに対応する `Session` ハンドルであり、`http_version` / `session_id` / `addr` が接続と一致する
- 配線 (h3 側の分岐) を外すと追加したテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- 統一 API の `Client.on_stop_sending` の追加。統一 `Client` には `on_stop_sending` が無く、HTTP/2 / HTTP/3 のどちらでもピアの停止要求を観測できない。本 issue は統一 `Server` の非対称を扱い、`Client` 側は別の対応にする
- `Server.on_error` の HTTP/3 対応 (h3 層に `on_error` が無く、別の対応になる)
- h3 層 / h2 層の `on_stop_sending` 自体の挙動 (0251 / 0176 の担当。本 issue は統一 API の配線のみ)
- ピアの STOP_SENDING を nghttp3 へ転送する契約 (0251 で実装済み)

## 解決方法

{後で追記}
