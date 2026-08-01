# WebTransport over HTTP/3 サーバーにストリームを開く API を追加する

- Created: 2026-08-01
- Completed: YYYY-MM-DD
- Branch: feature/add-server-open-stream
- Polished: 2026-08-01

## 目的

サーバーからクライアントへの一方的なストリーム送信 (server-initiated stream) を高レベル API で可能にする。現在は低レベル API でしか実現できず、高レベル `Server` ではサーバーからストリームを開けない (draft-ietf-webtrans-http3-16 Section 4.2「WebTransport endpoints can initiate unidirectional streams.」)。

## 現状

- `src/webtransport/h3/server.py` の `Server` は `send_stream_data` / `reset_stream` / `close_stream` / `send_datagram` を持つが、ストリームを新規に開く API が無い
- 低レベル API には `webtransport.h3.Session` の `open_stream` (`src/bindings/webtransport_h3.cpp`) と `quic.Connection` の `open_stream` (`src/bindings/quic.cpp`) が存在する
- クライアント側の高レベル `Client` には `open_stream` が既に存在する (`src/webtransport/h3/client.py`) が、サーバー側には無い

## 設計方針

- `Server` に `open_stream` を追加する。シグネチャは既存の `send_datagram` と同様にクライアントアドレス (`addr`) とセッション ID を引数で受け、`unidirectional` 引数を持つ (`async def open_stream(addr, session_id, unidirectional=True) -> int`)。戻り値は stream_id とし、失敗時は -1 を返す (`Client.open_stream` と対称)
- 本 issue では単方向ストリームのみを対象とし、双方向ストリームは対象外とする (`unidirectional=False` は実装しない)
- 実装は `Client.open_stream` (`src/webtransport/h3/client.py`) と同様に、`quic_connection.open_stream(False)` → `webtransport_session.open_stream(session_id, stream_id, True)` の順に呼ぶ (低レベル API は `quic` 側が bidirectional、`h3` 側が is_unidirectional と極性が反転している点に注意)。h3 側の登録が失敗した場合は -1 を返す。返された stream_id に対して既存の `send_stream_data` で送信できる
- 低レベル API の既存実装を利用する (`src/bindings/webtransport_h3.cpp` / `src/bindings/quic.cpp` の変更は伴わない)。クライアント側の変更も不要で、サーバーが開いたストリームは既存の `on_stream_data` で受信できる
- issue 0004 (ブラウザ e2e テスト) は「高レベル `Server` にストリームを開く API が無いため」低レベル API でテストサーバーを構築するとしている。本 issue の実装でこの前提は解消されるため、実装後に 0004 側の記述を更新する

## 完了条件

- 高レベル API でサーバーからクライアントへの単方向ストリーム送信ができる
- モックなしの e2e テストでクライアント側のストリーム受信を検証できる (既存の `test_unidirectional_stream` の逆方向で、クライアント側の変更は伴わない)
