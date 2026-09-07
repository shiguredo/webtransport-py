# 公開 Sans-IO API の引数や Config 値だけで依存ライブラリの assert に到達し SIGABRT する経路を塞ぐ

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-binding-arg-validation-abort
- Polished: 2026-09-07

## 目的

nanobind で公開している Sans-IO API に、Python から渡した引数や Config の値だけで ngtcp2 / nghttp3 の `assert()` に到達しプロセスが SIGABRT する経路が少なくとも 10 個存在する。バインディング内のコメント 7 箇所と doc 1 箇所は「Release ビルドでは assert が無効化されるため C++ 側でガードする」と書いているが、依存 3 ライブラリの CMake が Release でも `-DNDEBUG` を強制除去しているためこの前提は偽で、実際には全 assert が本番でも有効。公開 API の引数を検証してこれらの経路を全て塞ぐ。

## 現状

- 実験 (scratchpad `assert_probe.py`) で 10 経路すべてが終了コード 134 (SIGABRT) と `Assertion failed:` を出力することを確認した

| 経路 | 発火場所 (バインディング) | 依存側 assert |
|---|---|---|
| `quic.Config.max_data = 2**62` (他 `max_stream_data_*` も同様) | `src/bindings/quic.cpp` の `QuicConnection::initialize_client` / `initialize_server` / `initialize_server_from_packet` の TP 設定 | ngtcp2_conn.c の `conn_new` |
| `quic.Config.idle_timeout_ns = 2**64 - 1` | 同上 (`params.max_idle_timeout = ...`) | ngtcp2_conn.c の `conn_new` |
| `h3.Session.open_stream` を同じ stream_id で 2 回 | `src/bindings/webtransport_h3.cpp` の `H3Session::open_stream` (`stream_info_` 重複確認なし) | nghttp3_conn.c の `nghttp3_conn_open_wt_data_stream` |
| `h3.Session.open_stream` にクライアントで `stream_id % 4 == 1` (サーバー起点 bidi) | 同上 | 同上 |
| `h3.Session.set_max_client_streams_bidi` を減少値で呼ぶ | `src/bindings/webtransport_h3.cpp` の `H3Session::set_max_client_streams_bidi` | nghttp3_conn.c の `nghttp3_conn_set_max_client_streams_bidi` |
| `http3.Connection.submit_response` を QPACK 未バインドで呼ぶ | `src/bindings/http3.cpp` の `Http3Connection::submit_response` (`Http3Connection::submit_request` 等にはあるガードが欠落) | nghttp3_conn.c の `nghttp3_conn_submit_response` |
| `http3.Connection.bind_control_stream(2)` (サーバー) | `src/bindings/http3.cpp` の `Http3Connection::bind_control_stream` (`H3Session::bind_control_stream` は検証済み) | nghttp3_conn.c の `nghttp3_conn_bind_control_stream` |
| `http3.Connection.submit_request(1, ...)` (`stream_id % 4 != 0`) | `src/bindings/http3.cpp` の `Http3Connection::submit_request` | nghttp3_conn.c の `nghttp3_conn_submit_request` |
| `receive_stream_data(-1, ...)` (h3 / http3 両方) | `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` | nghttp3_conn.c の `nghttp3_conn_read_stream2` |
| `h3.Config.qpack_blocked_streams = 2**62` (他 `max_field_section_size` / `qpack_max_dtable_capacity` も同様) | `src/bindings/webtransport_h3.cpp` の `H3Session::initialize` の SETTINGS 設定 | nghttp3_conn.c の `conn_new` |

- 依存 3 ライブラリの `CMakeLists.txt` が `foreach(_build_type "Release" "MinSizeRel" "RelWithDebInfo") ... string(REGEX REPLACE "(^| )[/-]D *NDEBUG($| )" ...)` で NDEBUG を強制除去
- ビルド済み静的ライブラリに `__assert_rtn` の参照が ngtcp2 30 個 / nghttp3 16 個 / nghttp2 13 個残存 (`nm` で確認)
- バインディング内で「Release ビルドでは assert が無効化されるため」と書かれた誤ったコメント: `src/bindings/http3.cpp` の 7 箇所 (`submit_shutdown_notice`、`frame_payload_left`、`drained`、`stream_priority`、`set_max_client_streams_bidi`、`client_stream_priority`、`server_stream_priority`)、`src/bindings/http3.h` の 1 箇所 (`set_max_client_streams_bidi` の doc)

## 設計方針

- 上記 10 経路を全て、バインディング側で引数を検証して拒否する。経路別の拒否方式は次とする。quic Config / h3 Config / Http3Config の異常値は生成時検証とし、失敗は既存の生成失敗経路 (`RuntimeError`) で扱う。`open_stream` の重複と非自起点 ID・`submit_request` の非自起点 ID・`submit_response` の QPACK 未バインドは `False` を返す。`void` の `set_max_client_streams_bidi` (h3) と `bind_control_stream` (http3) および `size_t` 返しの `receive_stream_data` (h3 / http3) の不正入力は `std::invalid_argument` を投げて `ValueError` にする (nanobind の既定翻訳)
- 検証内容: quic Config の `max_data` / `max_stream_data_bidi_local` / `max_stream_data_bidi_remote` / `max_stream_data_uni` は varint 上限 (2^62 - 1、RFC 9000 Section 16) 以下、`idle_timeout_ns` は `UINT64_MAX` 未満とする。`max_streams_bidi` / `max_streams_uni` / `max_datagram_frame_size` に対応する assert は ngtcp2 側に無いため検証対象外とする。h3 Config と Http3Config の `max_field_section_size` / `qpack_max_dtable_capacity` / `qpack_blocked_streams` は varint 上限以下とする。`open_stream` で開けるのは自起点ストリームのみ (クライアントは `%4==0` と `%4==2`、サーバーは `%4==1` と `%4==3`) とし、`stream_info_` の重複確認を行う。`set_max_client_streams_bidi` は単調増加のみ受け付ける (`Http3Connection::set_max_client_streams_bidi` の実装を参考にする)。`open_stream` / `submit_request` の異常系再現には有効セッションの事前確立が必要である
- 誤ったコメント 7 箇所と doc 1 箇所を「依存 3 ライブラリは Release ビルドでも `-DNDEBUG` が除去され `assert` が本番でも有効」に置き換える
- 引数の上限は shiguredo-python 規約「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」にも整合する
- `H3Session::set_max_client_streams_bidi` に既にガードが入っている `Http3Connection::set_max_client_streams_bidi` と同じ形の実装を追加し、両者を対称にする

## 完了条件

- 上記 10 経路すべてで abort しないこと。Config 異常値は生成失敗 (`RuntimeError`)、`open_stream`・`submit_request`・`submit_response` の異常系は `False`、上限外の数値・不正 ID は `ValueError` になること
- バインディング内の 8 箇所の誤ったコメントが訂正されていること
- PBT を追加すること。Config の任意値 (0 〜 2^62 - 1) は `tests/prop_quic.py` (quic Config)・`tests/prop_http3.py` (Http3Config)・`tests/prop_webtransport_h3.py` (h3 Config) に、stream_id の任意値 (負値・パリティ違反を含む整数全域) は `tests/prop_webtransport_h3.py` と `tests/prop_http3.py` に分け、いずれも abort しないことを検証する
- 既存のテスト全 834 件が引き続き通過すること
