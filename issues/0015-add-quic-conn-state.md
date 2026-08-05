# ngtcp2 の接続状態・エラー・ピア情報 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-05
- Branch: feature/add-quic-conn-state
- Polished: 2026-08-04

## 目的

QUIC コネクションの状態・エラー詳細・ピア情報を Python から取得できるようにし、エラー診断と運用監視を可能にする。現在はエラーコード (ccerr) と TLS エラー詳細を取得する手段が無い (ConnectionClosed イベントの reason は粗い理由文字列のみ)。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` は `is_closed()` / `is_established()` / `is_handshake_completed()` / `get_connection_id()` 等の状態確認のみ公開しており、エラー詳細は取得できない
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用 (列挙した getter は 1 系が deprecated のため 2 系 (const ポインタ版) で列挙する)
  - `ngtcp2_conn_get_ccerr2`: コネクションエラー (エラーコードと理由文字列)
  - `ngtcp2_conn_get_tls_error2`: TLS エラーコード
  - `ngtcp2_conn_get_tls_alert2`: TLS アラート
  - `ngtcp2_conn_get_remote_transport_params2`: ピアのトランスポートパラメータ
  - `ngtcp2_conn_get_local_transport_params2`: ローカルのトランスポートパラメータ
  - `ngtcp2_conn_get_negotiated_version2`: ネゴシエーションされた QUIC バージョン
  - `ngtcp2_conn_get_client_chosen_version2`: クライアントが選択した QUIC バージョン
  - `ngtcp2_conn_in_closing_period2`: CLOSING 状態か
  - `ngtcp2_conn_in_draining_period2`: DRAINING 状態か
  - `ngtcp2_conn_get_scid2`: 送信元接続 ID (SCID)
  - `ngtcp2_conn_get_active_dcid3`: アクティブな宛先接続 ID (DCID)

## 設計方針

- `QuicConnection` に状態・エラー取得メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)。変更対象は `src/bindings/quic.cpp` / `src/bindings/quic.h` (メソッド追加・nanobind バインディング) とテスト (`tests/test_quic_error_handling.py` 等)。`src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- ngtcp2 の deprecated API (1 系) は使わず、2 系 (const ポインタ版) を使用する
- コネクションエラーは `error_code` (int) / `reason` (str) の 2 つの独立したプロパティで公開する (理由が NULL の場合は空文字)。エラーが無い場合 (ccerr の error_code が 0) はどちらも `None` を返す。ピアが NO_ERROR (0) の CONNECTION_CLOSE を送って正常終了した場合も error_code は 0 になり、エラー無し (None) と区別できない点に注意する。TLS エラーはコードとアラートを返す (エラー無し時は 0 をそのまま返す。0014 と同じく初期値を None に変換しない)
- トランスポートパラメータは主要フィールドのみを `remote_` / `local_` プレフィックス付きのプロパティで公開する (例: remote_max_idle_timeout / local_max_idle_timeout。対象は max_idle_timeout / max_udp_payload_size / initial_max_data / initial_max_stream_data_bidi_local / initial_max_stream_data_bidi_remote / initial_max_stream_data_uni / initial_max_streams_bidi / initial_max_streams_uni / max_datagram_frame_size。全フィールドの公開は行わない)
- ハンドシェイク前は ngtcp2 が初期値を返すため `None` にはならない (0014 と同じ方針。初期値 (0 / false / 空リスト) はそのまま返す)。例外は `get_remote_transport_params2` (NULL を返す場合があるため各フィールドのプロパティが None) と `get_ccerr2` (エラー無し = error_code 0 のため None。ハンドシェイク前後で常に適用)
- 本 issue の getter はコネクションが閉じた後も値を返す (ccerr / tls_error / tls_alert の主用途は閉じた後のエラー診断のため。0014 の「閉じている場合は None」パターンは適用しない)
- SCID / アクティブ DCID は複数返り得るため `list[bytes]` で公開する (アクティブ DCID は cid のみを公開し、seq / token 等は対象外とする)
- Python 側の公開名は 0014 と同じく ngtcp2 の API 名から `get_` を除いた形とする (ccerr は error_code / reason の 2 つの独立したプロパティ、tls_error / tls_alert はプロパティ、scid / active_dcid は `list[bytes]` のプロパティ。トランスポートパラメータの主要フィールドは remote_ / local_ プレフィックス付きのプロパティ)
- 0014 (接続統計) / 0016 (ストリーム制御) も同じ `QuicConnection` を変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python からコネクションエラーのコードと理由が取得できる (error_code / reason の 2 つの独立したプロパティ。エラーが無い場合はどちらも None)
- Python から TLS エラーとアラートが取得できる
- Python からリモート / ローカルのトランスポートパラメータ (主要フィールド) が取得できる
- Python からネゴシエーションされた QUIC バージョン・クライアント選択バージョン・SCID・アクティブ DCID・CLOSING / DRAINING 状態が取得できる
- モックなしのテストで、ハンドシェイク後とエラー発生時に値が取得できることを確認する (エラー発生は tls_alert を verify_callback の失敗 (証明書検証失敗) で発生させる。tls_error はこの経路では設定されない場合がある。ccerr は CONNECTION_CLOSE 受信でのみ設定されるが、現状の close() は CONNECTION_CLOSE を送出しないため、受信経路の確保は実測で判断し、送出実装が必要な場合は別 issue として切り出す)
- モックなしのテストで、ハンドシェイク前は `None` にならず初期値 (0 / false / 空リスト。SCID は 1 個以上) がそのまま返ることを確認する (`get_remote_transport_params2` と `get_ccerr2` のみ None)
- モックなしのテストで、コネクションを閉じた後も値が取得できることを確認する (ccerr は受信経路の制約に従う)

## 解決方法

`src/bindings/quic.cpp` / `quic.h` の `QuicConnection` に接続状態・エラー・ピア情報取得 API を追加し、nanobind で公開した (Python 側は `webtransport.quic.Connection`)。

- コネクションエラーを `error_code` / `reason` の 2 つの独立プロパティで公開する。エラー無し (ccerr の error_code が 0) はどちらも `None`。`reason` はピア制御の任意バイト列のため、Python 側では surrogateescape でデコードして例外を防ぐ
- TLS エラーとアラートを `tls_error` / `tls_alert` で公開する (エラー無し時は 0 をそのまま返す)
- トランスポートパラメータの主要 9 フィールドを `remote_` / `local_` プレフィックス付きプロパティで公開する (remote_ は未受信時に None)
- `negotiated_version` / `client_chosen_version` / `in_closing_period` / `in_draining_period` / `scid` (list[bytes]) / `active_dcid` (list[bytes]) を公開する
- deprecated の 1 系ではなく 2 系 (const ポインタ版) のみを使用する
- 本 getter 群はコネクションが閉じた後も値を返す (ccerr / tls_error / tls_alert は閉じた後のエラー診断用のため、0014 の「閉じている場合は None」パターンは適用しない)

テストは `tests/test_quic_conn_state.py` に追加した。`test_conn_state_before_handshake` (クライアント) と `test_conn_state_server_before_handshake` (accept 直後のサーバー) でハンドシェイク前の初期値を、`test_conn_state_after_handshake` でハンドシェイク後の値取得を、`test_conn_state_remote_reflects_peer_config` でピアの設定値が remote_* に反映されることを確認する。`test_conn_state_after_close` でクローズ後も値が取得できることを、`test_tls_alert_on_certificate_verification_failure` で verify_callback の失敗 (証明書検証失敗) により tls_alert が設定されることを確認する。ccerr の非 None 経路 (CONNECTION_CLOSE 受信) は受信経路の制約によりテスト不能のため、テストは None 側のみを検証する。
