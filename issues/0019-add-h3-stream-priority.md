# nghttp3 の優先度制御 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-04
- Branch: feature/add-h3-stream-priority
- Polished: 2026-08-04

## 目的

HTTP/3 の RFC 9218 (Extensible Prioritization Scheme) によるストリーム優先度の設定と、Priority ヘッダー値のパースを Python から行えるようにする。レスポンスの重要度に応じたスケジューリングが可能になる。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection` は優先度 API を公開しておらず、Python から優先度を制御できない (サーバー側で受信したリクエストの Priority ヘッダーのみ nghttp3 がスケジューリングへ自動適用する)
- 本家 nghttp3 (webtransport ブランチ) の以下の API が未使用
  - `nghttp3_conn_get_stream_priority2`: ストリームの現在の優先度の取得 (サーバーのみ)
  - `nghttp3_conn_set_client_stream_priority`: クライアント起動双方向ストリームの優先度設定 (クライアントのみ。PRIORITY_UPDATE フレームを送信する)
  - `nghttp3_conn_set_server_stream_priority`: クライアント起動双方向ストリームの優先度設定の上書き (サーバーのみ。サーバーが設定するとクライアントからの優先度更新は無視される)
  - `nghttp3_pri_parse_priority`: RFC 9218 の Priority ヘッダー値のパース

## 設計方針

- `Http3Connection` にメソッドを追加し、nanobind で公開する (Python 側は `webtransport.http3.Connection`)。変更対象は `src/bindings/http3.cpp` / `.h` (メソッド追加・nanobind バインディングと nb::sig) とテスト (`tests/test_http3_stream_priority.py`)、`src/webtransport/http3/__init__.py` (`parse_priority` の re-export。既存の `get_version` と同じパターン)。`src/webtransport/http3/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- 実装時に発見した既存の欠落として、`set_max_client_streams_bidi` も `Http3Connection` に追加する (H3Session には既に存在し、`src/webtransport/h3/server.py` が 100 を設定している)。nghttp3 は `nghttp3_ord_stream_id` が 1 始まりで `max_client_streams` の初期値が 0 のため、サーバー側で設定しないと PRIORITY_UPDATE フレームが `NGHTTP3_ERR_H3_ID_ERROR` で拒否される。`src/webtransport/http3/server.py` にも H3Session と同じく 100 を設定する (HANDSHAKE_COMPLETED イベントでストリームデータ処理より前に呼ぶ)。累積最大数は単調増加のみ許可され、C++ 側で減算を防ぐ
- Python 公開名は nghttp3 の API 名から `get_` / `set_` / `pri_` を除き、末尾のバージョン番号 (`2`) も除いた形とする: `stream_priority(stream_id: int) -> tuple[int, bool] | None` / `client_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool` / `server_stream_priority(stream_id: int, urgency: int, incremental: bool) -> bool` / `parse_priority(value: str) -> tuple[int, bool] | None`。`parse_priority` はコネクションに依存しないためモジュール直下の関数として公開する
- 優先度は `nghttp3_pri` 構造体のフィールド (urgency / inc) を `(urgency: int, incremental: bool)` タプルで受け渡しする (urgency は 0-7、incremental は bool)。`client_stream_priority` は nghttp3 がシリアライズ済み priority field value (文字列) を受け取る API のため、C++ 側でタプルから `u={urgency}` と incremental=true のときのみ `, i` を付けた形式 (例: `u=5, i`) にシリアライズして渡す (RFC 9218 の Dictionary キーは `u` / `i` のみで、`urgency` / `incremental` というキーは定義されない。それらは未知パラメータとして nghttp3 のパーサが黙って無視する)
- 利用できる側を C++ 側でガードする: `stream_priority` はサーバーのみ / `client_stream_priority` はクライアントのみ / `server_stream_priority` はサーバーのみ (nghttp3 の assert は get_stream_priority2 / set_server_stream_priority が conn->server、set_client_stream_priority が !conn->server)。assert は Release ビルドで無効化されているため、ガードは assert の有無に依存せず必要。ガード時とコネクションが閉じている場合は、getter は None、mutator は False を返す
- mutator (`client_stream_priority` / `server_stream_priority`) は成功で True / 失敗で False を返す。3 API とも stream_id の範囲 (負値と NGHTTP3_MAX_VARINT 超) を C++ 側でガードする (NGHTTP3_MAX_VARINT は nghttp3 の非公開マクロのため、C++ 側では (1LL << 62) - 1 をローカル定数として定義する)。mutator は urgency 0-7 / incremental bool の範囲チェックも C++ 側で行う (範囲外の値は、クライアント側ではピアが PRIORITY_UPDATE をパースできずコネクションエラーになる。サーバー側でも nghttp3 は assert (Release ビルドで無効化) でのみ範囲を検証するため)。`client_stream_priority` は制御ストリーム未バインド時は False を返す (nghttp3 は制御ストリーム未バインド時に NULL 参照するため。goaway() と同様のガード)。`stream_priority` はストリームが存在しない場合も None を返す。なお、`client_stream_priority` は PRIORITY_UPDATE フレームを送信するだけでクライアント自身の送信順序には反映されない (反映されるのはサーバー側のスケジューリング)
- `parse_priority` は nghttp3 のパーサが対象の構造体を初期化しないため、C++ 側でデフォルト (urgency=3 / incremental=false) で初期化してからパースする (RFC 9218 のデフォルト適用と同じ挙動)。パースに失敗した場合は None を返す (範囲外 (u=9 等)・型違い (i=1 等) は nghttp3 のパーサがエラーを返す。RFC 9218 は unknown / out-of-range / unexpected type の無視を求めているが、nghttp3 が無視を実装しているのは unknown のみで、out-of-range / unexpected type はエラーになる)。受信した Priority ヘッダーは既に Headers イベントで Python に届くため、受信経路 (recv_header_cb) は変更しない
- WebTransport (H3Session) には追加しない (draft-ietf-webtrans-http3 の 3.4 節は WebTransport セッション内のストリーム・データグラムに対する優先度シグナリングを定義しておらず、WebTransport のストリームには HTTP メッセージの優先度概念を適用しない。セッション自体の優先度 (CONNECT リクエストの Priority ヘッダーや PRIORITY_UPDATE) もこの issue の対象外とする)
- 0017 / 0018 / 0024 (http3.cpp) も同じファイルを変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python からストリームの優先度を取得できる (サーバーで `stream_priority(stream_id)` が `(urgency, incremental)` タプルを返す。Priority ヘッダーを含まないリクエストを受信したストリームではデフォルト (3, False) を返す。クライアントの `Http3Connection` で呼んだ場合と、ストリームが存在しない場合は None を返す)
- Python からストリームの優先度を設定できる (クライアントで `submit_request` によりストリームを生成した後に `client_stream_priority` を呼び、設定した優先度 (デフォルト以外の値、例: urgency=2 / incremental=True) が低レベル受け渡し構成 (_pump 方式) でサーバーに届き、サーバーの `stream_priority` で読める。サーバーの `server_stream_priority` で設定した値も `stream_priority` で読める)
- Python から RFC 9218 の Priority ヘッダー値をパースできる (`parse_priority` が `(urgency, incremental)` タプルを返す。キー省略時のデフォルト適用と不正値での None を含む)
- ガード経路も確認する (利用できない側での None / False、コネクションが閉じている場合の None / False、制御ストリーム未バインド時の False、存在しないストリームでの None / False、クライアント起動双方向でないストリーム ID での None / False、範囲外の引数での False)
- モックなしのテストで、取得・設定・パースが動作することを確認する (Http3Connection は低レベル受け渡し構成 (0017 と同様の `_pump` 方式) でテストする。0017 実装済みなら流用し、未実装なら 0013 と同様の構成を新規に構築する)

## 解決方法

`src/bindings/http3.cpp` / `.h` の `Http3Connection` に 3 つの優先度 API と `set_max_client_streams_bidi` を追加し、nanobind で公開した。`parse_priority` はコネクションに依存しないためモジュール直下の関数として公開し、`src/webtransport/http3/__init__.py` で re-export した。

- `stream_priority` (nghttp3_conn_get_stream_priority2): ストリームの優先度を (urgency, incremental) タプルで返す。サーバー専用。Priority ヘッダーなしのリクエストはデフォルト (3, False)
- `client_stream_priority` (nghttp3_conn_set_client_stream_priority): PRIORITY_UPDATE フレームで優先度を通知する。クライアント専用。RFC 9218 の Dictionary キーは u / i のみのため、タプルを "u={urgency}, i" 形式にシリアライズして渡す
- `server_stream_priority` (nghttp3_conn_set_server_stream_priority): 優先度を上書きする。サーバー専用。設定後はクライアントからの PRIORITY_UPDATE は無視される
- `set_max_client_streams_bidi` (nghttp3_conn_set_max_client_streams_bidi): 実装時に発見した既存の欠落として追加。nghttp3 は ord_stream_id が 1 始まりで max_client_streams の初期値が 0 のため、サーバー側で設定しないと PRIORITY_UPDATE が H3_ID_ERROR で拒否される。累積最大数は単調増加のみ許可 (C++ 側で減算を防ぐ)
- `parse_priority` (nghttp3_pri_parse_priority): RFC 9218 の Priority ヘッダー値をパースする。nghttp3 のパーサは対象を初期化しないため、デフォルト (3, false) で初期化してからパースする

`src/webtransport/http3/server.py` は HANDSHAKE_COMPLETED イベントで `setup_http3_streams` を呼び、その中で `set_max_client_streams_bidi(100)` を設定する (h3/server.py と同じパターン。クライアントからの PRIORITY_UPDATE を最初のフライトで受信できるように、ストリームデータの処理より前に呼ぶ)。

テストは `tests/test_http3_stream_priority.py` に追加した (22 件)。低レベル受け渡し構成 (`_pump` / `_create_connection_pair`) は 0017 と同様に構築し、デフォルト優先度・Priority ヘッダーの反映・PRIORITY_UPDATE の届達・サーバー設定の上書き・各種ガード (サーバー/クライアント限定・範囲外引数・存在しないストリーム・制御ストリーム未バインド) をモックなしで検証した。
