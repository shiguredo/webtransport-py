# WebTransport over HTTP/3 のアプリケーションエラーコードのリマップを実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-error-code-remap
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http3-16 Section 4.4 の MUST「WebTransport アプリケーションエラーコードを WT_APPLICATION_ERROR レンジ (0x52e4a40fa8db 〜 0x52e5ac983162、0x1f * N + 0x21 形式の予約済みコードポイントを除外) にリマップする」を実装する。現状はアプリの 32bit エラーコードがそのままワイヤに載り、コンプライアントなピアはレンジ外コードを「アプリエラーコードなし」として扱うため、アプリエラーが相手に伝わらない。

## 現状

- リマップ関数 (draft-16 Figure 4 の `webtransport_code_to_http_code` / `http_code_to_webtransport_code`) がコードベースに存在しない
- 送信時: アプリのエラーコードは H3Session のリセット経路 (`H3Session::reset_stream` → `close_stream` → `nghttp3_conn_close_stream`) を通り、nghttp3 の `reset_stream_cb` / `stop_sending_cb` が無変換で返し、Python 層が `QuicConnection::reset_stream` / `stop_sending` でそのままワイヤに載せる
- 受信時: QUIC の STREAM_RESET イベントの error_code がそのままアプリへ渡る (`src/webtransport/h3/client.py` / `server.py` のリセットイベント処理)
- リマップは nghttp3 / ngtcp2 が行わないためライブラリ側の責務
- 注意: `QuicConnection::reset_stream` / `stop_sending` (src/bindings/quic.cpp) は生 QUIC の汎用 API であり、プレーン HTTP/3 (http3/client.py の reset_stream) や WebTransport 内部の解放リセット (open_stream 登録失敗時等) でも使われる。WebTransport 以外の経路をリマップしてはならない

## 設計方針

- **変換関数の実装**: draft-16 Figure 4 の `webtransport_code_to_http_code` / `http_code_to_webtransport_code` を実装する (0x00000000 → 0x52e4a40fa8db、0xffffffff → 0x52e5ac983162、予約済み 0x1f * N + 0x21 をスキップ。レンジ内の非予約コードは 2^32 個でアプリコードと一対一対応)
- **送信時**: WebTransport データストリームのリセット / STOP_SENDING の送出時に、アプリの 32bit エラーコードを `webtransport_code_to_http_code` でリマップしてワイヤに載せる。適用箇所は WebTransport 専用の経路 (h3 層のリセット処理) に限定し、`QuicConnection::reset_stream` / `stop_sending` 自体には入れない (生 QUIC の汎用 API であり、プレーン HTTP/3 や内部解放リセットへ波及するため)。**リマップ位置は一意に決める**: 高レベル API の `reset_stream` (client.py / server.py) は `quic_connection.reset_stream` の直接呼び出しと nghttp3 経路 (`H3Session::reset_stream`) の両方に同じ error_code を渡すため、リマップを 1 箇所だけに置くと生コードがワイヤに載る (例: C++ の `H3Session::reset_stream` でデータストリーム判定と同時にリマップし、Python 側の直接呼び出しはリマップ済みコードを渡す構成にする)。**nghttp3 内部生成コード (セッション後始末時の WT_SESSION_GONE 等) はリマップしない**。なお `H3Session::close_stream` は送信 (reset_stream から) と受信 (STREAM_RESET 通知から) の両経路で呼ばれる共有関数のため、リマップは送信経路にのみ適用する
- **CONNECT ストリーム (セッション終了) のリセットはリマップ対象外**: Section 4.4 の MUST は WebTransport データストリームのリセットのみを対象とする。CONNECT ストリームのリセット (セッション終了、`close_stream` の `is_connect_stream` 分岐) は HTTP/3 エラーコード空間のまま残す。レンジ外判定の適用対象もデータストリームに限定し、CONNECT ストリームのリセットの error_code は判定対象外とする。データストリーム (`stream_info_`) と CONNECT ストリーム (`session_ids_`) の区別は `H3Session` 側で行う
- **受信時は逆変換しない**: Section 4.4 の MUST「受信したストリームリセットのエラーコードは変更せずに配信する (delivered unchanged)」に従い、ワイヤコードをそのままアプリへ渡す。Figure 4 の `http_code_to_webtransport_code` はアプリ側が受信コードを解釈するための参照実装であり、ライブラリが配信時に逆変換する必要はない。**レンジ外のワイヤコード (予約済みコード含む) を受信した場合は、ストリームはリセットとして扱いつつアプリエラーコードなしとして配信する** (SHOULD)。「エラーコードなし」の API 表現 (error_code を None にする等) を h3 層で決める。受信時の対象は RESET_STREAM / STOP_SENDING の両方である (draft Section 4.4)。現状 ngtcp2 の STOP_SENDING 受信コールバックが未登録のため STOP_SENDING の error_code はアプリに通知されていないが、この通知の有無 (エラーコードなし配信の対象に含めるか・通知しないままか) も本 issue で決める
- **32bit 範囲超過の送信エラーコード**: アプリが 0xffffffff を超える error_code を渡した場合はエラーとして扱う (例外を投げるか、h3 層で 32bit に収まるよう検証する)。H2 側の issue 0101 が受信時の「0xffffffff 超は WT_ERROR セッションエラー」を採用していることと、**「超える値をエラーとして扱う」方針の共通性**で整合させる (本 issue は送信時のアプリ入力検証であり、受信時の 0xffffffff 超は上記のレンジ外判定で処理される点が 0101 と異なる)
- リマップのテストを追加する: 送信時の変換 (ワイヤコードの観測)、受信時の無変換配信、レンジ外コードの「エラーコードなし」配信、往復 (アプリコード → ワイヤコード → アプリコード) の一対一対応、**WebTransport 以外の経路 (プレーン HTTP/3・内部解放リセット・CONNECT ストリームのリセット) がリマップされないことの検証**
- 既存 e2e テストの更新: リマップ後はワイヤコードが変わるため、ワイヤコードを直接観測するテストと、アプリへ届く error_code を検証するテストの期待値を更新する (受信側が無変換のため、アプリに届くのはリマップ済みワイヤコードになる)
- 変更内容を CHANGES.md の `## develop` に [ADD] として記載する

## 完了条件

- 送信時にアプリエラーコードが WT_APPLICATION_ERROR レンジへリマップされてワイヤに載る
- 受信時にワイヤコードがそのままアプリへ配信され、レンジ外コードは「アプリエラーコードなし」として配信される
- WebTransport 以外の経路 (プレーン HTTP/3・内部解放リセット・CONNECT ストリームのリセット) はリマップされない
- 32bit 範囲超過の送信エラーコードがエラーとして扱われる
- 上記がテストで検証できる
