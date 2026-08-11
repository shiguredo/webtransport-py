# クライアントの open_stream が失敗時に無効な stream_id を返す

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-client-open-stream-failure-handling
- Polished: {YYYY-MM-DD}

## 目的

高レベル `Client.open_stream` が h3 層の `open_stream` の戻り値 (false) を無視して stream_id をそのまま返すため、セッション終了後に呼ばれた場合に QUIC ストリームだけが開いた無効な stream_id が返り、ストリーム ID 空間を消費する問題を修正する。

## 現状

- `src/webtransport/h3/client.py` の `Client.open_stream` は `self._webtransport_session.open_stream(...)` の戻り値を無視して stream_id をそのまま返す
- セッション終了後 (open issue 0060 の修正で h3 層の `open_stream` が false を返すようになった) に呼ばれた場合:
  - QUIC ストリームは開かれる (`quic_connection.open_stream`)
  - h3 層への登録は行われないため、以後 `send_stream_data` は黙って無視される
  - QUIC ストリームはリセットされず、接続終了までストリーム ID 空間を消費する
  - ピアからは「WT ヘッダーもデータも来ないストリーム」として見える
- 対照的に `Server.open_stream` (src/webtransport/h3/server.py) は h3 層の false を受けて QUIC ストリームをリセットし -1 を返す (挙動が非対称)

## 設計方針

- `Client.open_stream` で h3 層の `open_stream` の戻り値を確認し、false の場合に QUIC ストリームをリセットして -1 を返す (Server.open_stream と対称化)
- 変更対象は `src/webtransport/h3/client.py`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- セッション終了後の `Client.open_stream` が -1 を返し、QUIC ストリームが開いたまま残らない (リセットされる)
- 生存セッションの `Client.open_stream` は従来どおり stream_id を返す
- モックなしのテストで検証できる (e2e 構成でサーバー側からセッション終了を注入する)
