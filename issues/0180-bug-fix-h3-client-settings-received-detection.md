# h3.Client.connect の SETTINGS 受信判定を stream_id==3 のヒューリスティックから recv_settings2_cb ベースに置き換える

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-client-settings-received-detection
- Polished: 2026-09-09

## 目的

`h3.Client.connect` は SETTINGS 受信完了を「サーバー制御ストリーム = stream_id 3 のデータ受信」で判定するが、RFC 9114 Section 6.2.1 は制御ストリームの ID を固定せず、サーバーが QPACK エンコーダーを先に開けば stream_id 3 は QPACK エンコーダーになる。加えてストリームタイプ 1 バイトのみ到達時にも真になり、`nghttp3_conn_submit_wt_request` が `conn_wt_enabled` 偽で失敗して `HandshakeFailedError("failed to send CONNECT request")` になる経路が開く。`H3Session::recv_settings2_cb` は no-op で SETTINGS 受信をアプリに通知しない。draft-ietf-webtrans-http3-16 Section 3.1「Clients MUST NOT attempt to establish WebTransport sessions ... until they have received the setting indicating WebTransport support」に照らして、明示的な SETTINGS 受信判定に置き換える。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` の 2 段目 (SETTINGS 待ちループ) が「サーバーの制御ストリーム (stream_id=3) からデータを受信したら設定完了とみなす」として `if quic_event.stream_id == 3: settings_received = True`
- ループ脱出後の判定も同じローカル変数 `settings_received` を使っており、ループ条件と本体のハードコード判定とループ後ガードの 3 箇所が連動している
- `src/bindings/webtransport_h3.cpp` の `H3Session::recv_settings2_cb` は `(void)` だけの no-op で SETTINGS 受信をアプリに通知しない
- `tests/test_e2e_webtransport_h3.py` の `_LowLevelClient` と `tests/test_debug_webtransport_h3.py` にも同じ `== 3` ヒューリスティックが複製されている
- draft-16 Section 3.1 の MUST
- 対照: h2 側は `H2Session::is_webtransport_ready()` (`webtransport_h2.cpp`) が SETTINGS の `WT_ENABLED` / `ENABLE_CONNECT_PROTOCOL` 受信を明示的に判定するアクセサを公開
- 既存 issue: 0122 「WebTransport over HTTP/3 の仕様追従の残りを対応する」の項目 3 に stream_id==3 ハードコードの項目あり (refresh 対象)

## 設計方針

- `H3Session::recv_settings2_cb` で SETTINGS 受信フラグを立てる
- `H3Session::is_webtransport_ready()` を追加し、SETTINGS の `wt_enabled` / `enable_connect_protocol` / `h3_datagram` が全て 1 かを確認するアクセサを公開する。3 フラグ照合の根拠は nghttp3 の `conn_wt_enabled` (クライアント側で上記 3 設定を要求する) であり、h2 と対称なのは命名・アクセサ形状のみである (h2 は 2 フラグ照合のため内容は非対称)
- `h3.Client.connect` の 2 段目ループは本体 (STREAM_DATA の `receive_stream_data` 呼び出しと CONNECTION_CLOSED 処理) を維持し、条件式を `while not self._webtransport_session.is_webtransport_ready() and self._running and loop.time() < deadline:` に置き換える。あわせてループ本体の `if quic_event.stream_id == 3: settings_received = True` とローカル変数 `settings_received` を削除し、ループ脱出後の判定を `if not self._webtransport_session.is_webtransport_ready():` に置き換える。本体の STREAM_DATA / CONNECTION_CLOSED 処理を変更すると nghttp3 へ SETTINGS が供給されなくなるため置換しない。`self._webtransport_session` は `_setup_streams` 済みで `Session` に絞り込まれ、ty の型検査が通るため `assert` や None チェックは追加しない
- `tests/test_e2e_webtransport_h3.py` の `_LowLevelClient` の複製のみ新 API に置き換える (`tests/test_debug_webtransport_h3.py` は 0188 の削除に委ねて触らない)
- 変更対象は `src/bindings/webtransport_h3.cpp` (フラグ設定・ムーブ引継ぎ・`bind_webtransport_h3` 内の nanobind `.def` 公開) と `src/bindings/webtransport_h3.h` (フラグ保管・`is_webtransport_ready` 宣言)、`src/webtransport/h3/client.py` (条件式とループ後判定の置換)、テスト、`CHANGES.md` とする。`h3.pyi` は `nanobind_add_stub` が生成するビルド生成物 (`*.pyi` は `.gitignore` 対象) のため手動更新しない

## 依存関係

- open issue 0122 項目 3 (イベント方式の `SettingsReceived` 案) と同一不具合を扱う。本 issue のアクセサ方式を採用し、0122 項目 3 の設計は陳腐化する。0122 本文の更新は 0122 側の作業として残る。実装 PR で 0122 項目 3 を「0180 で対応済み」に更新するか、着手前に 0122 を refresh して項目 3 を落とす

## 完了条件

- `h3.Client.connect` が制御ストリームの ID に依存せず SETTINGS 受信を判定できること
- サーバーが QPACK エンコーダーを先に開いた場合でも接続が成立すること
- `H3Session.is_webtransport_ready()` が Python から観測できること
- `tests/test_webtransport_h3_settings_ready.py` を新規作成し、Sans-IO でサーバー QPACK エンコーダーを制御より先に開設する順序で、エンコーダーのみ到着時は偽・制御到着後に真になる回帰テストを追加すること
- 既存のテスト全 976 件が引き続き通過すること
