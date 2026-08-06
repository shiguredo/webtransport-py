# nghttp3 のストリーム・接続制御 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-06
- Branch: feature/add-h3-stream-control
- Polished: 2026-08-04

## 目的

HTTP/3 ストリームの QUIC フロー制御ブロック制御と、QPACK デコーダーの同時ストリーム数ヒントを Python から行えるようにする。0017 (ストリーム状態確認 API) から分離した制御 API である。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session` と `src/bindings/http3.cpp` の `Http3Connection` はストリーム制御 API を公開しておらず、QUIC フロー制御ブロックの通知と同時ストリーム数ヒントの設定ができない
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_block_stream`: ストリームの QUIC フロー制御ブロックを通知
  - `nghttp3_conn_unblock_stream`: ストリームの QUIC フロー制御ブロック解除を通知
  - `nghttp3_conn_set_max_concurrent_streams`: QPACK デコーダーの内部リソース消費ヒント (decoder stream の長さ制限。現在値との max マージ)

## 設計方針

- `H3Session` と `Http3Connection` の両方にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.h3.Session` / `webtransport.http3.Connection`)。変更対象は `src/bindings/webtransport_h3.cpp` / `.h` と `src/bindings/http3.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_webtransport_h3_stream_control.py` / `tests/test_http3_stream_control.py`)。`src/webtransport/h3.pyi` / `http3/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- Python 側の公開名は nghttp3 の API 名から `set_` を除いた形とする (0019 と同じ規則): `block_stream(stream_id: int) -> None` / `unblock_stream(stream_id: int) -> bool` / `max_concurrent_streams(n: int) -> None` メソッド。既存の `set_max_client_streams_bidi` (webtransport_h3.cpp) は公開名規則の適用前の API であり対象外
- `unblock_stream` は成功で True / 失敗 (NOMEM) で False を返す (存在しないストリームも 0 (成功) を返す点に注意。NOMEM 経路はモック禁止のためテスト不能)。`block_stream` / `set_max_concurrent_streams` は void のため戻り値なし
- `block_stream` はクライアント双方向ストリーム (% 4 == 0) のみ即時にスケジューラから外し、単方向ストリームは FC_BLOCKED フラグが立つだけで書き込み経路がブロックを参照しないため、ブロック直後に 1 回の書き込みが通る (検証はクライアント双方向ストリームで行う。設計方針の完了条件参照)
- `set_max_concurrent_streams` は効果が外部から観測できない (現在値との max マージのため、小さい値は反映されない。実効下限は 100 で、nghttp3 の decoder stream 長さ制限の計算に使われる) ため、呼び出し後も通常のリクエスト送受信が継続できることを確認する
- コネクションが閉じている場合は no-op とする (`unblock_stream` は False)。`Http3Connection` 側は既存の `!conn_ || closed_` ガードと同じパターン (現行実装では closed_ が true になる経路が無いため防御的)。`H3Session` 側は既存メソッドと同じ `!conn_` ガードのみとする (H3Session の closed_ は GOAWAY 受信 (graceful shutdown) で true になるが、GOAWAY 後も既存ストリームのフロー制御ブロック操作は有効なため closed_ は見ない)
- 0018 は送信メッセージ拡張 (トレーラ・1xx・shutdown notice・shutdown_stream_write) を、本 issue はフロー制御・接続制御 (block / unblock / max_concurrent_streams) を担当する。0018 の shutdown_stream_write は「WebTransport への追加が必要になれば別途検討する」としていたが、本 issue の block_stream / unblock_stream は性質が異なる (QUIC フロー制御ブロックの通知であり、WT データストリームにも必要) ため H3Session に追加する
- 0009 / 0010 / 0017 (webtransport_h3.cpp / .h) と 0018 / 0019 (http3.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素。0023 (webtransport_h3.cpp) は polish-issue の必要性判断で不要と判定された (目的の達成不能・0010 の設計前提との矛盾) ため実装されない見込みであり対象外)

## 完了条件

- Python からストリームのブロック / アンブロックができる (クライアント双方向ストリーム (% 4 == 0) で、send → block → `get_streams_to_send` (データが出ない) → unblock → `get_streams_to_send` (データが再び出る) の順で確認する。block 前に flush してしまうと unblock 後も再スケジュールされないため、この順序を守る。単方向ストリームではブロック直後に 1 回の書き込みが通るため検証に使わない。0017 実装済みなら `stream_writable` が false / true になることも確認する)
- Python から同時ストリーム数のヒントを設定できる (`max_concurrent_streams` 呼び出し後も、H3Session はセッション確立とデータストリーム送受信が、Http3Connection は通常のリクエスト送受信が継続できることを確認する。効果は外部から観測できないため、これ以上の検証は行わない)
- ガード経路も確認する (存在しないストリーム ID での `unblock_stream` の True、`conn_` が無い場合の no-op / False)
- モックなしのテストで、各 API が動作することを確認する (H3Session は 0013 と同じ h3.Session 同士の直接受け渡し構成、Http3Connection は低レベル受け渡し構成 (0017 と同様の `_pump` 方式) でテストする。0017 実装済みなら流用し、未実装なら 0013 と同様の構成を新規に構築する)

## 解決方法

- `block_stream` / `unblock_stream` / `max_concurrent_streams` を `H3Session` (webtransport_h3.cpp) と `Http3Connection` (http3.cpp) に実装し、nanobind で公開した (Python 側は `webtransport.h3.Session` / `webtransport.http3.Connection`)
- ガードは設計方針通り `H3Session` は `!conn_` のみ、`Http3Connection` は `!conn_ || closed_` とした
- 検証はクライアント双方向ストリーム (% 4 == 0) で send → block → `get_streams_to_send` (データが出ない) → unblock → `get_streams_to_send` (データが再び出る) の順で行い、`stream_writable` の変化と届いた DATA の内容 (b"hello" / b"request-body") も確認した。ガード経路 (存在しないストリーム ID・負値・2**62 超過) もテストで確認した
- テスト実装中に既存バグを発見した: `fin=False` の `send_data` / `send_stream_data` で DATA フレームのペイロードが壊れる問題 (read_data コールバックが送信済みバッファを pop_front で解放し、nghttp3 が ALIEN 参照中の領域がダングリングポインタになる)。read_data コールバックの pop_front をイテレータでのスキップに変更し、解放は acked_stream_data コールバックに一本化して修正した
- テストは 419 件全て pass (HTTP/3 側 3 件、WebTransport over HTTP/3 側 3 件を追加)
