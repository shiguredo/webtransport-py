# HTTP/3 プロトコルエラーのエラーコード通知 API を追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http3-error-code-notification-api
- Polished: {YYYY-MM-DD}

## 目的

HTTP/3 プロトコルエラー発生時に、`nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` の負値 return から得られる内部エラーコード (`NGHTTP3_ERR_H3_FRAME_ERROR` 等) を RFC 9114 Section 8.1 の H3 ワイヤーエラーコード (`H3_FRAME_ERROR = 0x0106` 等) にマッピングし、`Http3EventType::Error` イベント + `Http3Event.error_message` フィールドとしてアプリケーションに通知する API を追加する。

0107 (closed 予定) が「握りつぶし止め + is_closed() 対称チェック + QUIC CONNECTION_CLOSE 送出 (error_code は暫定 `H3_GENERAL_PROTOCOL_ERROR = 0x0101` 固定)」だけを対応するのに対し、本 issue はそのフォローアップとして詳細エラー情報のアプリ通知経路を実装する。

## 現状

- 隣接する `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` は既に `nghttp3_conn_read_stream2` の負値検知時に `H3EventType::Error` イベントを push し、`event.error_code = static_cast<uint64_t>(-consumed)`・`event.error_message = nghttp3_strerror(...)` を載せている。`H3Event` 型には `std::string error_message` フィールドが存在する
- 一方 `src/bindings/http3.cpp` の `Http3EventType` (定義位置は `src/bindings/http3.h`) には `Error` バリアントが無く、`Http3Event` 型にも `error_message` フィールドが無い
- 0107 完了後の `Http3Connection::receive_stream_data` / `get_streams_to_send` は nghttp3 の負値時に `closed_ = true` を立てるだけで、エラー内容 (どの H3 エラーコードか) はアプリに通知されない
- `src/webtransport/http3.pyi` の `class EventType` にも `Error` に対応する enum 値が無く、`class Event` にも `error_message` プロパティが無い
- 高レベル `src/webtransport/http3/client.py` / `server.py` にも `on_connection_error` 相当のコールバック API が無い
- 0107 の QUIC `close(H3_GENERAL_PROTOCOL_ERROR, ...)` は暫定的な error_code 一律固定であり、実際の nghttp3 内部エラーに対応する H3 ワイヤーエラーコード (RFC 9114 Section 8.1) を送出できていない

## 設計方針

- `src/bindings/http3.h`:
  - `Http3EventType` に `Error` バリアントを追加 (`webtransport_h3.h` の `H3EventType::Error` と対称)
  - `Http3Event` 構造体に `std::string error_message` フィールドを追加
- `src/bindings/http3.cpp`:
  - `Http3Connection::receive_stream_data` の `nghttp3_conn_read_stream2` 負値分岐で、`closed_ = true` に加えて `Http3EventType::Error` イベントを push (`webtransport_h3.cpp` の `H3Session::receive_stream_data` line 156-166 と同じ形)
  - `Http3Connection::get_streams_to_send` の `nghttp3_conn_writev_stream` 負値分岐でも同様に Error イベントを push
  - nghttp3 内部エラーコード (`NGHTTP3_ERR_*`) から H3 ワイヤーコード (`H3_FRAME_ERROR = 0x0106` / `H3_FRAME_UNEXPECTED = 0x0105` / `H3_MESSAGE_ERROR = 0x010e` / `H3_ID_ERROR = 0x0108` / `H3_SETTINGS_ERROR = 0x0109` / `H3_STREAM_CREATION_ERROR = 0x0103` 等) へのマッピング関数を切り出して定義し、単体テスト可能にする
  - error_message は `nghttp3_strerror(consumed)` の返す文字列を採用
- `src/webtransport/http3.pyi`:
  - `EventType` 列挙に `Error` の enum 値を追加
  - `Event` クラスに `error_message` プロパティを追加
- `src/webtransport/http3/constants.py` (0107 で新設):
  - RFC 9114 Section 8.1 のエラーコード定数を追加 (`H3_NO_ERROR = 0x0100` / `H3_FRAME_ERROR = 0x0106` / `H3_FRAME_UNEXPECTED = 0x0105` / `H3_MESSAGE_ERROR = 0x010e` / `H3_ID_ERROR = 0x0108` / `H3_SETTINGS_ERROR = 0x0109` / `H3_STREAM_CREATION_ERROR = 0x0103` 等、必要な範囲に絞る)
- `src/webtransport/http3/client.py`:
  - `on_connection_error(callback: Callable[[int, str], Awaitable[None]])` を追加し、`Client.run` の HTTP/3 イベント処理ループで `Error` イベントを受けたら callback に `(error_code, error_message)` を渡して呼び出す
  - 0107 で追加した `is_closed()` チェックによる `close()` 呼び出し時に、直前の `Error` イベントで得た H3 ワイヤーコードを `close()` の error_code に使う (`H3_GENERAL_PROTOCOL_ERROR` 固定を Error イベント由来のコードに置き換える)
- `src/webtransport/http3/server.py`:
  - Client と対称に `on_connection_error(callback: Callable[[int, str, tuple[str, int]], Awaitable[None]])` を追加 (addr 引数付き)
  - 同様に `close()` の error_code を Error イベント由来に置き換える

## 完了条件

- `src/bindings/http3.h` の `Http3EventType` に `Error` バリアントが追加され、`Http3Event` に `error_message` フィールドが追加されている
- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `get_streams_to_send` が nghttp3 の負値時に Error イベントを push するようになっている
- `NGHTTP3_ERR_*` から RFC 9114 Section 8.1 の H3 ワイヤーコードへのマッピング関数が実装され、`tests/test_http3.py` (または適切な低レベルテストファイル) に単体テストがある
- `src/webtransport/http3.pyi` の `EventType` に `Error` enum 値、`Event` に `error_message` プロパティが追加されている
- `src/webtransport/http3/constants.py` に RFC 9114 Section 8.1 のエラーコード定数が追加されている
- `src/webtransport/http3/client.py` / `server.py` に `on_connection_error` コールバックが追加され、Error イベント受信時に発火する
- `Client.run` / `Server.run` の QUIC `close()` の error_code が Error イベント由来の H3 ワイヤーコードになっている (0107 の暫定 `H3_GENERAL_PROTOCOL_ERROR` 固定から置き換わる)
- e2e テストで、不正 HTTP/3 フレーム送信時にピア側の `on_connection_error` が正しい H3 ワイヤーコードとメッセージで発火することを検証する
- 既存 e2e テストがすべて pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/bindings/http3.h` に `Http3EventType::Error` と `Http3Event.error_message` を追加
- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `get_streams_to_send` の負値分岐で、`closed_ = true` に加えて Error イベントを push
- `src/bindings/http3.cpp` に nghttp3 内部エラー → H3 ワイヤーコードのマッピング関数を実装 (`static uint64_t nghttp3_error_to_h3_wire_code(int nghttp3_err)` 等)
- `src/webtransport/http3.pyi` を bindings と同期
- `src/webtransport/http3/constants.py` に RFC 9114 Section 8.1 定数を追加
- `src/webtransport/http3/client.py` / `server.py` に `on_connection_error` コールバックと Error イベント処理を追加、QUIC `close()` の error_code を Error イベント由来に変更
- `tests/test_http3.py` にマッピング関数の単体テストを追加
- `tests/test_e2e_http3.py` に `on_connection_error` 発火の e2e テストを追加

## 依存関係

- 本 issue は 0107 (closed 予定) の完了を前提とする。0107 が bindings 側の `closed_ = true` セットと高レベル層の `is_closed()` チェック + `close()` 呼び出しの基盤を作り、本 issue はその上に「Error イベントの中身」を載せる形になる
- 0107 の暫定 `H3_GENERAL_PROTOCOL_ERROR` 固定を本 issue で詳細コードに置き換えるため、0107 と同時に 1 PR にまとめない (レビューと revert の単位を分けるため、`shiguredo-git` の 1 issue 1 PR 原則に従う)
