# データグラムの不正なセッション ID 受信時に H3_ID_ERROR で接続を閉じる

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-id-error-validation
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 4 の MUST「endpoint が client-initiated bidirectional stream ID に対応しないセッション ID をデータグラムで受信した場合は、H3_ID_ERROR で接続を閉じる」を実装する。open issue 0027 で「受信検証の拡張は別作業」と線引きされた項目の受領。

## 現状

- `src/bindings/webtransport_h3.cpp` の `receive_datagram` は Quarter Stream ID を `session_id = quarter_stream_id * 4` で復元するだけで、正当なセッション ID (クライアント起動双方向ストリーム ID、%4==0) かどうかを検証しない。仕様逸脱ピアが 2^61 以上の巨大 varint を送った場合は int64 のラップで負のセッション ID になり得る
- 高レベル `Server` の `_process_webtransport_events` の DATAGRAM 分岐は負のセッション ID をそのまま `on_datagram` に渡す (0027 でフォールバックを削除済み)。負のセッション ID は無効としてアプリが扱う設計
- 仕様の MUST (Section 4 の H3_ID_ERROR での接続クローズ) は未実装であり、仕様逸脱ピアからの不正なデータグラムを接続を維持したまま処理する

## 設計方針

- 受信したデータグラムのセッション ID が正当 (クライアント起動双方向ストリーム ID、%4==0 かつ QUIC ストリーム ID 範囲内) でない場合、H3_ID_ERROR (RFC 9114 の HTTP/3 アプリケーションエラーコード) で接続を閉じる
- 検証の場所と接続クローズの伝播経路 (h3 層から QUIC 層へのエラーシグナル) を設計する。`receive_datagram` で検出してエラーイベントを生成し、高レベル層が QUIC の接続クローズを送出する形が既存のエラー経路と整合する可能性が高い
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` / `src/webtransport/h3/server.py` / テスト

## 完了条件

- 仕様逸脱ピアが巨大な Quarter Stream ID を持つデータグラムを送った場合、H3_ID_ERROR で接続が閉じられる
- 正常なセッション ID のデータグラムは従来どおり配送される
- モックなしのテストで検証できる
