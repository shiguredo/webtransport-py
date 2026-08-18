# WebTransport over HTTP/3 の SETTINGS / transport parameter 検証が no-op のままな問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-settings-validation
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http3-16 Section 3.1 の SETTINGS / transport parameter 検証 MUST が未実装のままである問題を修正する。現在はピアの SETTINGS / transport parameter を検証しないため、要件を満たさないピアとの間で WebTransport セッションを確立し得る。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::recv_settings2_cb` は完全な no-op で、受信 SETTINGS を一切検証しない
- 未達の MUST:
  - 「クライアントは SETTINGS_WT_ENABLED が 1 より大きい値を受信したら H3_SETTINGS_ERROR で接続を閉じる MUST」(draft Section 3.1。MUST の主体は **クライアントのみ**)。nghttp3 は受信時に `dest->wt_enabled = ent->value != 0` と boolean 正規化してからコールバックへ渡すため、`recv_settings2_cb` では値 2 を観測できない
  - 「サーバーの SETTINGS / transport parameter が要件を満たさない場合、クライアントはセッションを確立しない MUST」(transport parameter の max_datagram_frame_size > 0・reset_stream_at はどこでも検証されない)
  - 「クライアントの SETTINGS / transport parameter が要件を満たさない場合、サーバーは確立済み・新規の全セッションを malformed として扱う MUST」
- nghttp3 のサーバー分岐は remote の SETTINGS_WT_ENABLED / enable_connect_protocol を要求しない (interop 目的のコメントあり。remote の SETTINGS_H3_DATAGRAM は要求する)
- なお、SETTINGS_H3_DATAGRAM の値検証 (1 超は H3_SETTINGS_ERROR) は nghttp3 側で実施済み

## 設計方針

- **SETTINGS_WT_ENABLED > 1 の検出は nghttp3 側の変更が必要**。nghttp3 が受信時に値を boolean 正規化するため、`recv_settings2_cb` だけでは値 2 を検出できない。nghttp3 の webtransport ブランチ (deps.json で `branch: "webtransport"` 指定) の `nghttp3_conn_on_settings_entry_received` (SETTINGS_WT_ENABLED の処理分岐。互換用 ID の WT_MAX_SESSIONS / WT_MAX_SESSIONS_DRAFT7 / ENABLE_WEBTRANSPORT_DRAFT2 と case を共有している) で、**draft 版の SETTINGS_WT_ENABLED (0x2c7cf000) の値 > 1** を `NGHTTP3_ERR_H3_SETTINGS_ERROR` にする変更を加える。適用範囲は **クライアント側の受信時のみ** とする (draft の「値 > 1 は H3_SETTINGS_ERROR」MUST の主体はクライアントのみであり、サーバーが値 2 を受信した場合の正しい挙動は「全セッションを malformed として扱う」MUST のため、サーバー側は boolean 正規化を維持して malformed 扱いと整合させる)。互換用 ID は従来どおり受理するかは実装時に判断。受け渡し経路は open 中の issue 0092 と同様 (上流 PR、間に合わなければ deps.json の参照先固定) とする。本 issue の実装は 0092 と並行し得るが、両者が `recv_settings2_cb` と nghttp3 依存に触れるため、実装順序と変更の衝突を考慮する
- `recv_settings2_cb` では、nghttp3 から通知される検証済みの SETTINGS 状態を基に、クライアント側で「サーバーの SETTINGS が要件を満たすか」を検証する。クライアント側の検証項目は draft Section 3.1 の 3 項目 (SETTINGS_WT_ENABLED = 1 / SETTINGS_ENABLE_CONNECT_PROTOCOL = 1 / SETTINGS_H3_DATAGRAM = 1)。なお、クライアントはサーバーの SETTINGS 受信まで CONNECT を送らない MUST と 3 項目未達ならセッションを確立しない MUST は nghttp3 の `nghttp3_conn_submit_wt_request` が `conn_wt_enabled` で担保済みのため、本 issue のクライアント側 SETTINGS 検証は、要件未達時に接続を閉じる場合の手段 (draft の MAY である WT_REQUIREMENTS_NOT_MET で接続を閉じる) を実装する用途に限定する
- **クライアント側**でサーバーの transport parameter 要件を検証する: max_datagram_frame_size > 0 は既存の `remote_max_datagram_frame_size()` (src/bindings/quic.cpp) で取得できる。reset_stream_at の remote 値は getter が存在しないため、ngtcp2 の `ngtcp2_transport_params` の `reset_stream_at` を参照する getter を追加して検証する
- **サーバー側**でクライアントの SETTINGS / transport parameter を検証し、要件未達なら「確立済み・新規の全セッションを malformed として扱う」を実行する。なお、サーバー側の SETTINGS 検証 (remote の h3_datagram 要求・確立済みセッションの abort・新規 CONNECT 拒否) は nghttp3 の `conn_wt_enabled` / `abort_wt_session` が既に実施済みのため、本 issue のサーバー側実装対象は主に **transport parameter 検証と、要件未達時の malformed 扱いの整合** とする (nghttp3 の既存挙動との重複実装を避ける)。具体挙動 (確立済みセッションの終了方法・受理前リクエストの拒否方法) は実装時に RFC 9114 Section 4.1.2 を参照して決める。draft のとおり、サーバーはクライアントの SETTINGS 受信前に CONNECT が届き得るため、SETTINGS 未受信時の扱い (保留 or 無効扱い) も決定する
- テストはモック不使用の規約に従い、実 QUIC / HTTP/3 スタックで不正な SETTINGS を送る構成にする。nghttp3 の送信側は SETTINGS_WT_ENABLED の値を 1 に固定している (`nghttp3_stream.c` の SETTINGS 書き出し) ため、値 2 の SETTINGS を送出する手段 (送信コードのテスト用変更 or テスト専用の送出経路) を用意する

## 完了条件

- クライアントが SETTINGS_WT_ENABLED の値 2 を受信したときに H3_SETTINGS_ERROR で接続が閉じる
- クライアントがサーバーの transport parameter 要件 (max_datagram_frame_size > 0 / reset_stream_at) を検証し、要件未達のサーバーとセッションを確立しない
- サーバーがクライアントの SETTINGS / transport parameter を検証し、要件未達なら確立済み・新規の全セッションを malformed として扱う
- 上記のテストが追加され通る
