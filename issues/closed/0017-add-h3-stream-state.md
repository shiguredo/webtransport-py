# nghttp3 のストリーム状態確認 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-04
- Branch: feature/add-h3-stream-state
- Polished: 2026-08-04

## 目的

HTTP/3 ストリームの書き込み可否・送信完了・受信状況を Python から確認できるようにする。現在は書き込み可否や送信状況を確認する手段が無く、アプリケーションは送信タイミングを制御できない。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session` と `src/bindings/http3.cpp` の `Http3Connection` はストリーム状態を公開しておらず、`is_closed()` 程度の確認しかできない
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_is_stream_writable2`: ストリームが書き込み可能か (存在しない・closed・フロー制御ブロック・入力データ待ち・half-closed の判定)
  - `nghttp3_conn_is_stream_flushed`: ストリームの全送信データが QUIC スタックに受け渡し済みか (write offset ベース。ACK ではない)
  - `nghttp3_conn_get_frame_payload_left2`: 受信中のフレーム残量 (クライアント双方向ストリームまたはリモート制御ストリーム以外は 0)
  - `nghttp3_conn_is_drained2`: `nghttp3_conn_shutdown` 後のアクティブリモートストリーム 0 判定 (サーバー専用・コネクション単位)
  - `nghttp3_conn_get_stream_wt_session_id`: ストリームが属する WebTransport セッション ID

## 設計方針

- `H3Session` と `Http3Connection` の両方にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.h3.Session` / `webtransport.http3.Connection`)。変更対象は `src/bindings/webtransport_h3.cpp` / `.h` (H3Session) と `src/bindings/http3.cpp` / `.h` (Http3Connection) (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_webtransport_h3_stream_state.py` / `tests/test_http3_stream_state.py` 等)。`src/webtransport/h3.pyi` / `http3/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- クラス別の割り当て: `get_stream_wt_session_id` は WebTransport 専用のため `H3Session` のみに公開する。`is_drained2` はサーバー専用・コネクション単位のため `Http3Connection` のみに公開し、C++ 側でサーバーのみガードする (クライアントで呼ぶと Debug ビルドでは nghttp3 の assert で abort、Release ビルドでは意味のない値を返す可能性があるため。クライアント時の戻り値は None。`H3Session` は `nghttp3_conn_shutdown` を呼ぶ経路が無く常に false になるため公開しない)。`get_frame_payload_left2` は WebTransport データストリーム (単方向 % 4 == 2 / 3。双方向の WT データストリームも受信処理でフレーム残量を消費しない) では機能しないため `H3Session` には公開しない (HTTP/3 のリクエストストリームで機能する `Http3Connection` のみ)。その他は両方に公開する
- ストリームが存在しない場合の戻り値は API ごとに異なる (`is_stream_writable2` / `get_frame_payload_left2` は 0、`is_stream_flushed` は 1 (非ゼロ)、`get_stream_wt_session_id` は -1)。通常時は `None` への変換を `get_stream_wt_session_id` のみ行う (他は nghttp3 の戻り値をそのまま返す)。`stream_writable` はフラグベースであり、受信専用 (リモート起動単方向) ストリームでもフラグが立っていなければ true を返す点に注意する
- コネクションが閉じている場合は、getter は全て `None` を返す (0 / 非 0 の int として公開する `stream_writable` / `stream_flushed` / `frame_payload_left` も None。既存パターン)
- Python 側の公開名は 0014-0016 と同じく nghttp3 の API 名から `is_` / `get_` を除き、末尾のバージョン番号 (`2`) も除いた形とする (例: `stream_writable(stream_id)` / `stream_flushed(stream_id)` / `frame_payload_left(stream_id)` / `drained` (引数を取らない getter のためプロパティ) / `stream_wt_session_id(stream_id)` メソッド)。`get_frame_payload_left2` の引数には nghttp3 の assert があるため、負の stream_id と `NGHTTP3_MAX_VARINT` 超の stream_id は C++ 側でガードする
- 0009 / 0010 (webtransport_h3.cpp) と 0018 / 0019 (http3.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)。`drained` の検証は `goaway()` (= `nghttp3_conn_shutdown`) の現行セマンティクスに依存するため、0018 が goaway() の意味を変更する場合は本検証との調整が必要

## 完了条件

- Python からストリームの書き込み可否・送信完了 (QUIC スタックへの受け渡し済み)・ドレイン状態が取得できる (ドレイン状態は `Http3Connection` のサーバー側のみ。クライアントの `Http3Connection` で `drained` が None を返すことも確認する。`stream_writable` は受信専用ストリームでも true を返す場合がある点をテストで前提としない)
- Python から受信中フレームの残量が取得できる (Http3Connection。HTTP/3 のリクエストストリームで検証する)
- Python からストリームの WebTransport セッション ID が取得できる (H3Session。存在しないストリームは None)
- モックなしのテストで、各 API が動作することを確認する (H3Session は 0013 と同じ h3.Session 同士の直接受け渡し構成、Http3Connection は新規に低レベル受け渡し構成 (0013 と同様の `_pump` 方式) を構築する。`drained` はサーバー側で `goaway()` → リモート双方向ストリーム 0 を経て true になることを確認する)

## 解決方法

`src/bindings/webtransport_h3.cpp` / `.h` (H3Session) と `src/bindings/http3.cpp` / `.h` (Http3Connection) にストリーム状態確認メソッドを追加し、nanobind で公開した。

- `H3Session` に `stream_writable` (nghttp3_conn_is_stream_writable2) / `stream_flushed` (nghttp3_conn_is_stream_flushed) / `stream_wt_session_id` (nghttp3_conn_get_stream_wt_session_id) を追加
- `Http3Connection` に `stream_writable` / `stream_flushed` / `frame_payload_left` (nghttp3_conn_get_frame_payload_left2) / `drained` (nghttp3_conn_is_drained2、サーバーのみ) を追加
- コネクションが無いか閉じている場合は None を返す。`frame_payload_left` は nghttp3 の assert を避けるため負の stream_id と varint 最大値超を C++ 側でガード (ガード時は 0)。`drained` はサーバー以外と制御ストリーム未バインド時に None
- `stream_wt_session_id` は存在しないストリームと WebTransport データストリームでないストリーム (CONNECT ストリーム自身を含む) に None を返す (-1 を変換)
- `stream_flushed` は存在しないストリームに 1 (受け渡し済み扱い) を返す nghttp3 の仕様を doc に明記

テストは `tests/test_webtransport_h3_stream_state.py` (h3.Session 同士の直接受け渡し構成) と `tests/test_http3_stream_state.py` (Http3Connection の低レベル受け渡し構成を新規構築) に追加した。`frame_payload_left` は DATA フレームを 4 バイトずつ受信して残量の単調減少 (14 → 10 → 6 → 2 → 0) を検証し、`drained` はサーバーで `close_stream` → `goaway` → 送信処理の順で true になることを検証した。
