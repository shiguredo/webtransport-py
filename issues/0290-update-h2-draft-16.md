# WebTransport over HTTP/2 を draft-ietf-webtrans-http2-16 に追従する

- Created: 2026-11-03
- Completed: {YYYY-MM-DD}
- Branch: feature/update-h2-draft-16
- Polished: {YYYY-MM-DD}

## 目的

2026-11-02 公開の draft-ietf-webtrans-http2-16 (`refs/webtrans/draft-ietf-webtrans-http2-16.txt`) に実装を追従させる。

draft-16 は SETTINGS_WT_ENABLED の codepoint を 0x2b60 から 0x2b67 に変更し、フロー制御カプセルの受信値検証を「前回受信値より小さい場合はエラー」から「前回受信値、またはカプセル未受信なら初期値を増加させない場合はエラー」に強化した。現状の実装は draft-15 の codepoint と「減少のみエラー」の判定のままで、draft-16 のピアと相互運用できない。draft-16 準拠のピアを送信元とする E2E テストを成立させるには、受信側の検証を仕様どおりにする必要がある。

## 現状

- `src/bindings/webtransport_h2.h` の `SETTINGS_WT_ENABLED` は 0x2b60 (draft-15) で、送出と受信の双方がこの値のみを扱う
- `H2Session::handle_wt_max_data` / `H2Session::handle_wt_max_stream_data` / `H2Session::handle_wt_max_streams` の受信値検証は「前回受信値より小さい場合のみ」エラーであり、等値を受理する。draft-16 Section 6.5 / 6.6 / 6.7 は「増加しない場合」を MUST 違反とする
- `H2Session::apply_peer_initial_flow_control` は対向 SETTINGS の初期値が 0 の場合に `received_max_data` / `received_max_streams_bidi` / `received_max_streams_uni` を記録しないため、初期値 0 を基準にした非増加判定ができない (draft-16 Section 6.5 / 6.7 は「カプセル未受信なら初期値」を基準とする)
- セッション確立時に SETTINGS と同じ値の初期フロー制御カプセル (WT_MAX_DATA / WT_MAX_STREAMS) を送出しており、draft-16 の非増加 MUST を自分から違反する
- `tests/test_webtransport_h2_flow_control_capsule.py` には「同一値はエラーにしない」ことを固定したテストがある
- コードコメント、テスト、`skills/webtransport-py/SKILL.md` は draft-15 を参照している

## 設計方針

- `SETTINGS_WT_ENABLED` を draft-16 の 0x2b67 に変更する
- 旧 codepoint 0x2b60 も併送・受理する。HTTP/2 の SETTINGS は未知の設定 ID を無視する規定 (RFC 9113 Section 6.5) のため、両方送っても旧実装には影響せず、WebKit の WebTransport over HTTP/2 実装など旧 codepoint のピアとの相互運用を維持できる
- 対向 SETTINGS の初期値 (0 を含む) を `WtSessionInfo` の `received_max_data` / `received_max_streams_bidi` / `received_max_streams_uni` に記録し、カプセル受信値が増加しない場合は WT_FLOW_CONTROL_ERROR でセッションを閉じる
- WT_MAX_STREAM_DATA は draft-16 Section 4.3 の「SETTINGS と WebTransport-Init の大きい方」を初期値とする既存処理 (`H2Session::advertised_stream_send_credit`) を維持したまま、非増加判定に変更する
- SETTINGS と同じ値の初期フロー制御カプセルを送出しない。初期値は SETTINGS と WebTransport-Init ヘッダーで伝達済みであり、draft-16 の非増加 MUST により等値のカプセルはピアが接続を閉じるためである
- コードコメントの仕様引用を draft-16 の文言 (stream error → session error、RFC 8941 → RFC 9651 など) に合わせる
- `skills/webtransport-py/SKILL.md` の h2 側の版数表記を draft-16 に更新する

## 完了条件

- draft-16 の codepoint 0x2b67 で SETTINGS_WT_ENABLED を送受信できる
- 旧 codepoint 0x2b60 のみを送るピアとも WebTransport を確立できる
- 初期値 (SETTINGS 値。0 を含む) と等しい、またはそれより小さい WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS を受信したら WT_FLOW_CONTROL_ERROR でセッションを閉じる
- セッション確立時に SETTINGS と同じ値の初期フロー制御カプセルを送出しない
- 既存テストを draft-16 の期待値に更新し、非増加判定と codepoint 互換のテストを追加する
- WebKit を使った WebTransport over HTTP/2 のブラウザ E2E が通る
- 全テストが通る
