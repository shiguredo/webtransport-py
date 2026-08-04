# nghttp2 のメッセージング拡張 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-h2-message-ext
- Polished: 2026-08-04

## 目的

HTTP/2 のトレーラ送信・優先度更新 (RFC 9218)・Server Push・ALPN 選択を Python から行えるようにする。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection` は `submit_request` / `submit_response` / `send_data` / `reset_stream` / `goaway` / `ping` を公開しているが、トレーラ・優先度・Server Push・ALPN は扱えない
- 本家 nghttp2 (v1.70.0) の以下の API が未使用
  - `nghttp2_submit_trailer`: トレーラ送信 (トレーラ HEADERS が END_STREAM を担う)
  - `nghttp2_submit_priority_update`: RFC 9218 の PRIORITY_UPDATE フレーム送信 (クライアントのみ。シリアライズ済み priority field value を受け取る)
  - `nghttp2_session_change_extpri_stream_priority`: サーバー側の優先度設定 (RFC 9218。サーバーのみ)
  - `nghttp2_submit_push_promise`: Server Push の宣言 (サーバーのみ。promised stream ID を返す)
  - `nghttp2_select_alpn`: ALPN プロトコルの選択 (サーバー用ユーティリティ。h2 / http/1.1 の優先順で選択)
- `nghttp2_session_change_stream_priority` (RFC 7540 優先度ツリー) は RFC 9113 で廃止され、nghttp2 でも noop (常に 0 を返す) のため対象外とする

## 設計方針

- `Http2Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http2.Connection`)。変更対象は `src/bindings/http2.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig・受信イベントの追加) とテスト (`tests/test_http2_message_ext.py`)、`src/webtransport/http2/__init__.py` (`select_alpn` の re-export。既存の `get_version` と同じパターン)。`select_alpn` はコネクションに依存しないためモジュール直下の関数として公開する (0019 の `parse_priority` と同じ)。`src/webtransport/http2/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- Python 公開名は nghttp2 の API 名から `nghttp2_` / `nghttp2_session_` プレフィックスを除いた形とする (既存の `submit_request` / `submit_response` と同じ): `submit_trailer(stream_id: int, headers: list[tuple[str, str]]) -> bool` / `submit_priority_update(stream_id: int, urgency: int, incremental: bool) -> bool` / `change_extpri_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool` / `submit_push_promise(stream_id: int, headers: list[tuple[str, str]]) -> int` / `select_alpn(client_protocols: list[str]) -> str | None`。mutator は成功で True / 失敗で False を返す (submit_push_promise は promised stream ID / 失敗で -1)
- `submit_trailer` はトレーラ HEADERS が END_STREAM を担う。呼び出し順序は `send_data(stream_id, data, eof=False)` → `submit_trailer(stream_id, headers)` → send() で flush の順とする (eof=True の DATA をフラッシュすると END_STREAM 付きになり、half-closed (local) のため以降トレーラを送れない。RFC 9113 5.1 節・8.1 節。HTTP/3 の 0018 が fin=True で積むのと逆なのは、H3 は frq 経路の制約で fin=False では EOF 経路がなく、H2 は eof=True だと DATA に END_STREAM が付くため)。Python 側の `submit_trailer` は保留トレーラの記録のみを行い、`nghttp2_submit_trailer` の呼び出しは `data_source_read_callback` がデータの最終チャンクを返す時点で行う (EOF と `NGHTTP2_DATA_FLAG_NO_END_STREAM` を同時に立てる。nghttp2.h の「nghttp2_submit_trailer() can be called inside this callback」が根拠。直接キューに積むとヘッダー系が DATA より先に送信されるため、DATA → トレーラ HEADERS の順序を保てない)。トレーラを送らない場合は従来どおり eof=True で終端する。対象はサーバー側のレスポンストレーラのみ (クライアントの `submit_request` はデータプロバイダを渡しておらずリクエストボディを送信できないため、リクエストトレーラは対象外。クライアントで呼ばれた場合は C++ 側でガードし False を返す)。受信側は既存の HEADERS イベントで届く (トレーラも本体ヘッダーと同じ扱い。0018 と同じ判断)
- `submit_priority_update` は `(urgency, incremental)` タプルから `u={urgency}` と incremental=true のときのみ `, i` を付けた形式 (例: `u=5, i`) に C++ 側でシリアライズして渡す (0019 と同じ)。クライアントのみ (nghttp2 はサーバーセッションで NGHTTP2_ERR_INVALID_STATE を返す)。動作にはピア (サーバー) が `SETTINGS_NO_RFC7540_PRIORITIES=1` を送信している必要がある (nghttp2 は受信した remote_settings を参照し、未受信時は noop で 0 を返す)。ガード時は False を返す
- `change_extpri_stream_priority` は `nghttp2_extpri` 構造体のフィールド (urgency / inc) を `(urgency, incremental)` タプルで受け渡しする。サーバーのみ (nghttp2 はクライアントセッションで NGHTTP2_ERR_INVALID_STATE を返す)。ignore_client_signal は常に 1 を渡す (サーバーが設定した優先度を優先し、クライアントからの優先度更新を無視する。0019 の `server_stream_priority` と同じセマンティクス)。動作には自己が `SETTINGS_NO_RFC7540_PRIORITIES=1` を送信している必要がある (nghttp2 は送信した SETTINGS を参照し、未送信時は noop で 0 を返す)。両 API の動作条件に対応するため、`Http2Config` に `no_rfc7540_priorities` フィールド (デフォルト true) を追加し、`initialize()` の SETTINGS に含める (既存ユーザーの SETTINGS 内容が変わる変更であることを明記しておく)。ガード時は False を返す
- `submit_push_promise` は親ストリーム ID とヘッダーを受け取り、promised stream ID (成功時) または -1 (失敗時) を返す。サーバーのみ (nghttp2 はクライアントセッションで NGHTTP2_ERR_PROTO を返す)。受信側は `EventType::PushPromise` を追加し、`on_frame_recv_callback` に NGHTTP2_PUSH_PROMISE ケース・`on_begin_headers_callback` / `on_header_callback` の PUSH_PROMISE 対応・`Http2Event` への promised_stream_id フィールド追加で、promised stream ID とヘッダーを通知する
- 受信側の対応として `EventType::PriorityUpdate` も追加し、サーバー側の `on_frame_recv_callback` で NGHTTP2_PRIORITY_UPDATE を受信したら stream_id 付きで通知する (優先度更新がピアに届いたことの確認用。nghttp2 は受信した優先度をスケジューリングへ自動適用するため、受信側での処理は不要)。PRIORITY_UPDATE は拡張フレームのため、セッション作成時に `nghttp2_option_set_builtin_recv_extension_type(option, NGHTTP2_PRIORITY_UPDATE)` を設定し `nghttp2_session_server_new2` でサーバーセッションを作成する必要がある (現行の initialize は option なしの `nghttp2_session_server_new` を使用しているため変更が必要)
- `select_alpn` はクライアントが提示したプロトコルリスト (`list[str]`) を受け取り、h2 / http/1.1 の優先順で選択して `str` を返す (一致なしは None)。C++ 側で length-prefixed のワイヤ形式に変換して `nghttp2_select_alpn` を呼ぶ (nghttp2 の戻り値は 1 = h2 選択 / 0 = http/1.1 選択 / -1 = 一致なし)
- WebTransport over HTTP/2 (`H2Session`) には追加しない (H2Session は Http2Connection とは独立した nghttp2 セッションを管理しており、プレーン HTTP/2 のメッセージングとは目的が異なる)
- 0020 / 0021 (http2.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python からトレーラを送信できる (サーバーがレスポンストレーラを送信し、クライアント側の HEADERS イベント (DATA の後) で確認する)
- Python からストリーム優先度を更新できる (クライアントの `submit_priority_update` が送出され、サーバー側の PriorityUpdate イベントで確認する。サーバーの `change_extpri_stream_priority` は成功で True を返すことを確認する (ローカルなスケジューリング変更のみでワイヤ上の効果がないため、返り値での確認とする))
- Python から Server Push を宣言できる (サーバーの `submit_push_promise` が送出され、クライアント側の PushPromise イベントで promised_stream_id とヘッダーを確認する)
- Python から ALPN を選択できる (`select_alpn` が h2 / http/1.1 を正しく選択する。一致なしは None)
- ガード経路も確認する (利用できない側 (サーバーの `submit_priority_update` / クライアントの `change_extpri_stream_priority` / `submit_push_promise` / `submit_trailer`) での False / -1、コネクションが閉じている場合)
- モックなしのテストで、各 API が動作することを確認する (Http2Connection は低レベル受け渡し構成でテストする。クライアントとサーバーの両方の `Http2Connection` を用意して互いの送信データを受信側に流す構成は、既存の `tests/prop_http2_roundtrip.py` の `create_client_server_pair` / `exchange_settings` パターンを流用・拡張して構築する)
