# WebTransport over HTTP/3 の transport parameter 検証が no-op のままな問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-23
- Branch: feature/fix-h3-transport-param-validation
- Polished: 2026-08-18

## reopened にした理由

- 2026-08-21 に nghttp3 上流 (webtransport branch のメンテナ) から、SETTINGS_WT_ENABLED > 1 のクライアント側検証 (draft-ietf-webtrans-http3-16 §3.1 MUST) について「厳密化したいのでなければ現状維持で問題ない (このあたりは常に変更があるため)」との回答を受領した
- SETTINGS_WT_ENABLED > 1 検証を実現するには nghttp3 fork + patch + `deps.json` の参照先固定が必要になるが、上流方針を踏まえるとメンテナンスコストに見合わないため、本 issue のスコープから当該検証を除外する
- 加えて、上流 nghttp3 の `conn_wt_enabled` (`lib/nghttp3_conn.c`) にある interop 目的の緩和 (サーバーがクライアントの `SETTINGS_WT_ENABLED` を要求しない) の理由が「webtransport-go が `SETTINGS_WT_ENABLED` を送ってこないため」であることも上流から共有されたため、本 issue のサーバー側実装はこの緩和方針に追随する
- 残る 2 項目 (クライアント側の transport parameter 検証、サーバー側の transport parameter 検証) は nghttp3 依存がなく、本リポジトリ単独で実装可能なため reopened にしてスコープを絞り直す

## 目的

draft-ietf-webtrans-http3-16 Section 3.1 の QUIC transport parameter 検証 MUST が未実装のままである問題を修正する。現在はピアの transport parameter を検証しないため、要件を満たさないピア (`max_datagram_frame_size` が未設定または 0、`reset_stream_at` が未設定など) との間で WebTransport セッションを確立し得る。

## 対応範囲

- クライアント側で、サーバーの QUIC transport parameter (`max_datagram_frame_size > 0`、`reset_stream_at`) を検証し、要件未達ならセッションを確立しない
- サーバー側で、クライアントの QUIC transport parameter を検証し、要件未達なら確立済み・新規の全 WebTransport セッションを malformed として扱う

## スコープ外 (見送り or 別扱い)

- **SETTINGS_WT_ENABLED > 1 検証 (見送り)**: 上流 nghttp3 メンテナが「現状維持で OK」と表明していること、および nghttp3 が受信値を boolean 正規化 (`nghttp3_conn_on_settings_entry_received` 内で `dest->wt_enabled = ent->value != 0`) してから `recv_settings2_cb` に渡すためバインディング側の callback では raw value を観測できないこと、の 2 点から本 issue では対応しない。将来的に nghttp3 側の設計が変わったときに再検討する
- **サーバー側の SETTINGS_WT_ENABLED 未受信の許容 (上流方針を維持)**: 上流の `conn_wt_enabled` は webtransport-go との interop 目的でクライアントの `SETTINGS_WT_ENABLED` を要求しない設計になっている。本 issue はこの方針に追随する
- **SETTINGS 単体の値検証**: `SETTINGS_H3_DATAGRAM` の値 > 1 検証は nghttp3 側で実施済み。`SETTINGS_ENABLE_CONNECT_PROTOCOL` / `SETTINGS_H3_DATAGRAM` / `SETTINGS_WT_ENABLED` の要件充足の判断も nghttp3 の `nghttp3_conn_submit_wt_request` が `conn_wt_enabled` を通じて担保しているため、本 issue では扱わない
- **draft §3.1 MAY の `WT_REQUIREMENTS_NOT_MET` による接続クローズ**: 要件未達時の接続クローズ手段は MAY であり、上記のとおり要件充足の判断は nghttp3 側で担保されているため、本 issue では扱わない

## 現状

- QUIC transport parameter の getter は `src/bindings/quic.cpp` に `remote_max_datagram_frame_size()` が存在するが、`reset_stream_at` の remote 値を取得する getter は未実装
- WebTransport セッションを受理・確立する経路 (クライアント側は `H3Session` の CONNECT 送出前後、サーバー側は CONNECT 受信時) では transport parameter の検証を行っていない
- 未達の MUST:
  - クライアント: サーバーの transport parameter (`max_datagram_frame_size > 0` と `reset_stream_at`) を検証してセッション確立を判断すること
  - サーバー: クライアントの transport parameter を検証して、要件未達なら全セッションを malformed として扱うこと

## 設計方針

- **transport parameter の getter 追加**: ngtcp2 の `ngtcp2_transport_params.reset_stream_at` を参照する getter を `src/bindings/quic.cpp` に追加する (既存の `remote_max_datagram_frame_size()` と同様のパターン)
- **クライアント側検証**: WebTransport セッション確立前のタイミング (CONNECT 送出前ないし応答受領前) にサーバーの transport parameter を検証する。要件未達なら CONNECT を送らずセッションを閉じる。既存の `remote_max_datagram_frame_size()` と新規追加の `reset_stream_at` getter を利用する
- **サーバー側検証**: WebTransport セッションを受理する経路 (CONNECT 受信時) にクライアントの transport parameter を検証する。要件未達なら受理せず、確立済みセッションがあれば malformed 扱いで終了する。具体挙動は RFC 9114 Section 4.1.2 を参照して決める
- **テスト**: モック不使用の規約に従い、実 QUIC / HTTP/3 スタックで transport parameter を意図的に欠落させたピア (`max_datagram_frame_size = 0` / `reset_stream_at` 未設定) を用意して検証する。ngtcp2 のクライアント/サーバー側で transport parameter の書き出しをテスト用に制御する経路が必要になる

## 完了条件

- クライアントがサーバーの transport parameter 要件 (`max_datagram_frame_size > 0` / `reset_stream_at`) を検証し、要件未達のサーバーとセッションを確立しない
- サーバーがクライアントの transport parameter を検証し、要件未達なら確立済み・新規の全セッションを malformed として扱う
- 上記のテストが追加され通る

## 解決方法

- `src/bindings/quic.cpp` / `quic.h`: `QuicConnection::remote_reset_stream_at()` getter を追加し、`QuicConfig::enable_reset_stream_at` (既定 true、テスト用の欠落制御) を追加。reset_stream_at の広告を 4 箇所 (early data context / client / server / server from packet) すべてフラグで制御する
- `src/webtransport/h3/_transport_params.py` (新規): クライアント・サーバー共通の検証ヘルパー `meets_transport_param_requirements(conn)` を追加
- `src/webtransport/h3/client.py`: コンストラクタに `quic_config` を追加。connect() のハンドシェイク完了直後 (CONNECT 送出前) にサーバーの transport parameter を検証し、要件未達なら `False` を返す
- `src/webtransport/h3/server.py`: コンストラクタに `quic_config` を追加。SESSION_READY (CONNECT 受信) 時にクライアントの transport parameter を検証し、要件未達なら H3_MESSAGE_ERROR (0x010E) で接続を閉じる (RFC 9114 Section 4.1.2)
- テスト: `tests/test_e2e_webtransport_h3.py` に欠落 TP ピアとの e2e テスト 6 件 (datagram / reset_stream_at / both、クライアント側・サーバー側)、`tests/test_quic.py` に低レベル getter / config テスト 3 件を追加
