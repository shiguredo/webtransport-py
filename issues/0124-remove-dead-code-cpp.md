# C++ バインディングの死にコードを削除する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-dead-code-cpp
- Polished: {YYYY-MM-DD}

## 目的

C++ バインディング層に残る死にコード (機能していない設定項目・生成されないイベント・更新されないフィールド・仕様に存在しないヘッダー) を削除し、コードベースを現状の仕様に整合させる。

## 現状

- **旧 draft 用ヘッダー**: `src/bindings/webtransport_h3.cpp` の `H3Session::accept_session` が 200 応答に `sec-webtransport-http3-draft: draft02` を付与する。draft-ietf-webtrans-http3-16 全文にこのヘッダーの出現はなく、draft-02 時代の名残。テスト・Python 層からの参照もゼロ
- **常に false の死にフィールド**: `src/bindings/webtransport_h3.h` の `H3Event::is_unidirectional` は設定箇所がなく、常にデフォルト値 false が返る
- **生成されない enum 値**: `H3EventType::StreamOpened` (`src/bindings/webtransport_h3.h`)、`Http3EventType::PushPromise` (`src/bindings/http3.h`。HTTP/3 ではサーバープッシュが仕様から削除済み)、`Http3EventType::Reset` (`src/bindings/http3.h`。「後方互換」とコメントされるが対象利用者が不明)、`QuicEventType::ConnectionIdRetired` (`src/bindings/quic.h`。retire を検知しないため生成されない)
- **機能していない設定項目**: `Http2Config::send_preface` (`src/bindings/http2.h`) は `Http2Connection::initialize` が一切参照しない。`send_preface=False` でも nghttp2 がクライアントプリフェイスを自動送信し、設定が機能しない
- **死にフィールド**: `H2Session::goaway_sent_` (`src/bindings/webtransport_h2.h`) はムーブコンストラクタ以外で読み書きされない (h2 層に goaway() 自体がない)
- **未使用の StreamState 値**: `src/bindings/webtransport_h2.h` の `StreamState` の Ready / Send / DataSent / DataRead / ResetRead は定義のみで使用されない
- **空実装コールバック**: `QuicConnection::acked_stream_data_offset_cb` (`src/bindings/quic.cpp`) は return 0 のみで、コメントと実装が食い違う

## 設計方針

- 上記をすべて削除する (enum 値の削除は Python 側・テスト側で参照がないことを確認してから行う)
- 意図的に残すもの (後方互換の Reset 等) は利用者を明記したコメントを付けて残す判断も可
- `WtSessionInfo::max_streams_bidi_remote` / `max_streams_uni_remote` は受信ストリーム数上限の検証で読み出すため対象外とする

## 完了条件

- 上記の死にコードが削除され、ビルド・全テストが通る
