# 公開 Sans-IO API の引数や Config 値だけで依存ライブラリの assert に到達し SIGABRT する経路を塞ぐ

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-binding-arg-validation-abort
- Polished: {YYYY-MM-DD}

## 目的

nanobind で公開している Sans-IO API に、Python から渡した引数や Config の値だけで ngtcp2 / nghttp3 の `assert()` に到達しプロセスが SIGABRT する経路が少なくとも 10 個存在する。バインディング内のコメント 8 箇所は「Release ビルドでは assert が無効化されるため C++ 側でガードする」と書いているが、依存 3 ライブラリの CMake が Release でも `-DNDEBUG` を強制除去しているためこの前提は偽で、実際には全 assert が本番でも有効。公開 API の引数を検証してこれらの経路を全て塞ぐ。

## 現状

- 実験 (scratchpad `assert_probe.py`) で 10 経路すべてが終了コード 134 (SIGABRT) と `Assertion failed:` を出力することを確認した

| 経路 | 発火場所 (バインディング) | 依存側 assert |
|---|---|---|
| `quic.Config.max_data = 2**62` (他 `max_stream_data_*` も同様) | `QuicConnection::initialize_client` / `initialize_server` / `initialize_server_from_packet` の TP 設定 | ngtcp2_conn.c の `conn_new` |
| `quic.Config.idle_timeout_ns = 2**64 - 1` | 同上 (`params.max_idle_timeout = ...`) | ngtcp2_conn.c の `conn_new` |
| `h3.Session.open_stream` を同じ stream_id で 2 回 | `H3Session::open_stream` (`stream_info_` 重複確認なし) | nghttp3_conn.c の `nghttp3_conn_open_wt_data_stream` |
| `h3.Session.open_stream` にクライアントで `stream_id % 4 == 1` (サーバー起点 bidi) | 同上 | 同上 |
| `h3.Session.set_max_client_streams_bidi` を減少値で呼ぶ | `H3Session::set_max_client_streams_bidi` | nghttp3_conn.c の `nghttp3_conn_set_max_client_streams_bidi` |
| `http3.Connection.submit_response` を QPACK 未バインドで呼ぶ | `Http3Connection::submit_response` (`Http3Connection::submit_request` 等にはあるガードが欠落) | nghttp3_conn.c の `nghttp3_conn_submit_response` |
| `http3.Connection.bind_control_stream(2)` (サーバー) | `Http3Connection::bind_control_stream` (`H3Session::bind_control_stream` は検証済み) | nghttp3_conn.c の `nghttp3_conn_bind_control_stream` |
| `http3.Connection.submit_request(1, ...)` (`stream_id % 4 != 0`) | `Http3Connection::submit_request` | nghttp3_conn.c の `nghttp3_conn_submit_request` |
| `receive_stream_data(-1, ...)` (h3 / http3 両方) | `H3Session::receive_stream_data` / `Http3Connection::receive_stream_data` | nghttp3_conn.c の `nghttp3_conn_read_stream2` |
| `h3.Config.qpack_blocked_streams = 2**62` (他 `max_field_section_size` / `qpack_max_dtable_capacity` も同様) | `H3Session::initialize` の SETTINGS 設定 | nghttp3_conn.c の `conn_new` |

- 依存 3 ライブラリの `CMakeLists.txt` が `foreach(_build_type "Release" "MinSizeRel" "RelWithDebInfo") ... string(REGEX REPLACE "(^| )[/-]D *NDEBUG($| )" ...)` で NDEBUG を強制除去
- ビルド済み静的ライブラリに `__assert_rtn` の参照が ngtcp2 30 個 / nghttp3 16 個 / nghttp2 13 個残存 (`nm` で確認)
- バインディング内で「Release ビルドでは assert が無効化されるため」と書かれた誤ったコメント: `src/bindings/http3.cpp` の 8 箇所 (`bind_control_stream`、`bind_qpack_encoder_stream`、`bind_qpack_decoder_stream`、`submit_request`、`submit_response`、`set_max_client_streams_bidi`、`client_stream_priority`、`server_stream_priority`)、`src/bindings/http3.h` の 1 箇所 (`set_max_client_streams_bidi` の doc)

## 設計方針

- 上記 10 経路を全て、バインディング側で引数を検証して `ValueError` / `False` / 早期 return で拒否する
- 検証内容: Config の各 uint64 フィールドは QUIC / HTTP/3 の varint 上限 (2^62 - 1) 以下、`idle_timeout_ns` は `UINT64_MAX` 未満、stream_id は非負・パリティ・役割 (クライアント / サーバー起点)、`open_stream` は `stream_info_` の重複確認、`set_max_client_streams_bidi` は単調増加 (`Http3Connection::set_max_client_streams_bidi` の実装を参考にする)、`submit_response` は QPACK 未バインドで false
- 誤ったコメント 8 + 1 箇所を「依存 3 ライブラリは Release ビルドでも `-DNDEBUG` が除去され `assert` が本番でも有効」に置き換える
- 引数の上限は shiguredo-python 規約「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」にも整合する
- `H3Session::set_max_client_streams_bidi` に既にガードが入っている `Http3Connection::set_max_client_streams_bidi` と同じ形の実装を追加し、両者を対称にする

## 完了条件

- 上記 10 経路すべてで abort ではなく `ValueError` / `False` が返ること
- バインディング内の 9 箇所の誤ったコメントが訂正されていること
- `tests/prop_*.py` に、Config の任意値 (0 〜 2^62 - 1) と stream_id の任意値 (負値・パリティ違反を含む) で abort しないことを検証するプロパティテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
