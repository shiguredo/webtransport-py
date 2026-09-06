# h3.Client.connect の SETTINGS 受信判定を stream_id==3 のヒューリスティックから recv_settings2_cb ベースに置き換える

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-client-settings-received-detection
- Polished: {YYYY-MM-DD}

## 目的

`h3.Client.connect` は SETTINGS 受信完了を「サーバー制御ストリーム = stream_id 3 のデータ受信」で判定するが、RFC 9114 は制御ストリームの ID を固定せず、サーバーが QPACK エンコーダーを先に開けば stream_id 3 は QPACK エンコーダーになる。加えてストリームタイプ 1 バイトのみ到達時にも真になり、`nghttp3_conn_submit_wt_request` が `conn_wt_enabled` 偽で失敗して `HandshakeFailedError("failed to send CONNECT request")` になる経路が開く。`H3Session::recv_settings2_cb` は no-op で SETTINGS 受信をアプリに通知しない。draft-ietf-webtrans-http3-16 Section 3.1「Clients MUST NOT attempt to establish WebTransport sessions ... until they have received the setting indicating WebTransport support」に照らして、明示的な SETTINGS 受信判定に置き換える。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` の 2 段目 (SETTINGS 待ちループ) が「サーバーの制御ストリーム (stream_id=3) からデータを受信したら設定完了とみなす」として `if quic_event.stream_id == 3: settings_received = True`
- `src/bindings/webtransport_h3.cpp` の `H3Session::recv_settings2_cb` は `(void)` だけの no-op で SETTINGS 受信をアプリに通知しない
- `tests/test_e2e_webtransport_h3.py` の `_LowLevelClient` と `tests/test_debug_webtransport_h3.py` にも同じ `== 3` ヒューリスティックが複製されている
- draft-16 Section 3.1 の MUST
- 対照: h2 側は `H2Session::is_webtransport_ready()` (`webtransport_h2.cpp`) が SETTINGS の `WT_ENABLED` / `ENABLE_CONNECT_PROTOCOL` 受信を明示的に判定するアクセサを公開
- 既存 issue: 0122 「WebTransport over HTTP/3 の仕様追従の残りを対応する」の項目 3 に stream_id==3 ハードコードの項目あり (refresh 対象)

## 設計方針

- `H3Session::recv_settings2_cb` で SETTINGS 受信フラグを立てる
- `H3Session::is_webtransport_ready()` (仮) を追加し、SETTINGS の `wt_enabled` / `enable_connect_protocol` / `h3_datagram` が全て 1 かを確認するアクセサを公開する (h2 と対称)
- `h3.Client.connect` の 2 段目のループを `while not self._webtransport_session.is_webtransport_ready() and self._running and loop.time() < deadline:` に置き換える
- `tests/test_e2e_webtransport_h3.py` の `_LowLevelClient` と `tests/test_debug_webtransport_h3.py` の複製を新 API に置き換える (`test_debug_*` の削除計画とも整合)
- 既存 issue 0122 の該当項目を closed にする (refresh 経由)

## 完了条件

- `h3.Client.connect` が制御ストリームの ID に依存せず SETTINGS 受信を判定できること
- サーバーが QPACK エンコーダーを先に開いた場合でも接続が成立すること
- `H3Session.is_webtransport_ready()` が Python から観測できること
- `tests/` に SETTINGS 受信タイミング (ストリーム開設順が異なる) の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
