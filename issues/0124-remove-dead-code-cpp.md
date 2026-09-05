# C++ バインディングの死にコードを削除する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-dead-code-cpp
- Polished: 2026-09-03

## 目的

C++ バインディング層に残る死にコード (機能していない設定項目・生成されないイベント・更新されないフィールド・仕様に存在しないヘッダー) を削除し、コードベースを現状の仕様に整合させる。

## 現状

- **旧 draft 用ヘッダー**: `src/bindings/webtransport_h3.cpp` の `H3Session::accept_session` が 200 応答に `sec-webtransport-http3-draft: draft02` を付与する。`refs/webtrans/draft-ietf-webtrans-http3-16.txt` にこのヘッダーの出現はなく、draft-02 時代の名残。テスト・Python 層からの参照もゼロ
- **常に false の死にフィールド**: `src/bindings/webtransport_h3.h` の `H3Event::is_unidirectional` は設定箇所がなく、常にデフォルト値 false が返る (`StreamInfo::is_unidirectional` とは別フィールド)
- **生成されない enum 値**: `H3EventType::StreamOpened` (`src/bindings/webtransport_h3.h`)、`Http3EventType::PushPromise` (`src/bindings/http3.h`。生成するコールバックの登録がなく、生成されない)、`Http3EventType::Reset` (`src/bindings/http3.h`。生成されず `ResetStream` のみが生成される。ただし `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` が `RESET` を `RESET_STREAM` と同列に処理するため、削除時は Python 層の同時修正が必要)、`QuicEventType::ConnectionIdRetired` (`src/bindings/quic.h`。retire を検知しないため生成されない)
- **機能していない設定項目**: `Http2Config::send_preface` (`src/bindings/http2.h`) は `Http2Connection::initialize` が一切参照しないため、値によらず設定が反映されない
- **死にフィールド**: `H2Session::goaway_sent_` (`src/bindings/webtransport_h2.h`) はムーブコンストラクタ以外で読み書きされない (h2 層に goaway() 自体がない。`Http2Connection::goaway_sent_` は `goaway()` の二重送出ガードとして live のため対象外)
- **未使用の StreamState 値**: `src/bindings/webtransport_h2.h` の `StreamState` の Send / SizeKnown / DataRead / ResetRead は定義のみで使用されない (`Ready` と `Recv` は `send_state` と `recv_state` の初期値として参照されるため対象外。`DataSent` / `ResetSent` / `DataRecvd` / `ResetRecvd` は送受信ガードと状態遷移で使用されるため対象外)
- **空実装コールバック**: `QuicConnection::acked_stream_data_offset_cb` (`src/bindings/quic.cpp`) は return 0 のみで、コメントと実装が食い違う。宣言は `src/bindings/quic.h`、登録は `src/bindings/quic.cpp` の 3 箇所にある。ただし open issue 0144 が当該コールバックを再送保持のために実装するため、本 issue では対象外とする (0144 を先行させる)

## 設計方針

- 上記をすべて削除する。CODEBASE.md の「下位互換を維持しないこと」に従い、後方互換のための残置はしない
- enum 値の削除時は参照側も同時に更新する。対象は `tests/test_webtransport.py` の `STREAM_OPENED` 存在確認、`tests/test_http3.py` の `PUSH_PROMISE` 存在確認、`tests/test_quic.py` の `CONNECTION_ID_RETIRED` 存在確認、`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` の `RESET` 処理分岐。nanobind のバインディング定義変更に伴い pyi 生成物を再生成する
- `acked_stream_data_offset_cb` は宣言 (`src/bindings/quic.h`)・定義・登録 3 箇所 (`src/bindings/quic.cpp`) をすべて削除する。ただし open issue 0144 が実装するため対象外とし、本 issue では扱わない
- `WtSessionInfo::max_streams_bidi_remote` / `max_streams_uni_remote` は受信ストリーム数上限の検証で読み出すため対象外とする

## 完了条件

- 上記の死にコードが削除され、参照側 (テスト・Python 層・pyi 生成物) の更新が済み、ビルド・全テストが通る
