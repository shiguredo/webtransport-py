# データグラムの不正なセッション ID 受信時に H3_ID_ERROR で接続を閉じる

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-id-error-validation
- Polished: 2026-08-10

## 目的

draft-ietf-webtrans-http3-16 Section 4 の MUST「endpoint が client-initiated bidirectional stream ID に対応しないセッション ID をデータグラムで受信した場合は、H3_ID_ERROR で接続を閉じる」を実装する。issue 0027 で「受信検証の拡張は別作業」と線引きされた項目の受領であり、サーバーとクライアントの両エンドポイントでデータグラム受信時の検証を行う。

## 現状

- `src/bindings/webtransport_h3.cpp` の `receive_datagram` は Quarter Stream ID を `session_id = quarter_stream_id * 4` で復元するだけで、正当なセッション ID (クライアント起動双方向ストリーム ID) かどうかを検証しない。仕様逸脱ピアが巨大 varint を送った場合、セッション ID が QUIC ストリーム ID 範囲 2^62-1 を超える (2^60 以上 2^61 未満の Quarter Stream ID では正のまま範囲超過、2^61 以上では int64 への変換でビット 63 が立って負のセッション ID になる)
- 高レベル `Server` の `_process_webtransport_events` の DATAGRAM 分岐は負のセッション ID をそのまま `on_datagram` に渡す (0027 でフォールバックを削除済み)。負のセッション ID は無効としてアプリが扱う設計
- 仕様の MUST (Section 4 の H3_ID_ERROR での接続クローズ) は未実装であり、仕様逸脱ピアからの不正なデータグラムを接続を維持したまま処理する
- 既存テスト `tests/test_e2e_webtransport_h3.py` の `test_datagram_negative_session_id_passed_through` は「負のセッション ID がそのまま `on_datagram` に渡る」ことを固定しており、本 issue の実装で期待値が変わる (docstring も「MUST を実装する場合は本テストの期待値を合わせて更新すること」と予告している)

## 設計方針

- 受信したデータグラムのセッション ID が正当 (クライアント起動双方向ストリーム ID) でない場合、H3_ID_ERROR (RFC 9114 の HTTP/3 アプリケーションエラーコード、0x0108) で接続を閉じる。検証は構造のみで行い、`session_ids_` のメンバーシップは確認しない (draft-ietf-webtrans-http3-16 Section 4 の「Session IDs that correspond to closed sessions are not considered invalid for the purposes of this check」に従う。閉じたセッションの ID は検査対象外)
- 検証条件の実質は範囲チェックのみである。`session_id = quarter_stream_id * 4` の導出上 `%4==0` は常に成立するため、実質的な不正判定は「QUIC ストリーム ID 範囲内 (0 ≦ session_id ≦ 2^62-1)」の範囲外 (負の値と 2^62 以上を含む) のみである。`receive_datagram` で検出して H3 エラーイベントを生成する
- 接続クローズの伝播経路は新規に設計する。`H3EventType::Error` イベントを利用するが、現状の高レベル層 (`_process_webtransport_events`) には ERROR 分岐が存在しないため、`server.py` / `client.py` の両方に ERROR ハンドラを追加し、`quic_connection.close(0x0108)` で CONNECTION_CLOSE を生成・送出して接続を終了する (既存のエラー経路との整合は前提としない)
- ERROR ハンドラはデータグラム検証由来のエラーのみを対象とする。`H3EventType::Error` は既存の `receive_stream_data` (`src/bindings/webtransport_h3.cpp` の `receive_stream_data`) でも nghttp3 エラー時に生成されるため、それらを H3_ID_ERROR で接続クローズしないよう、データグラム検証由来であることを区別する (例: データグラム検証のエラーイベントにのみ 0x0108 を設定する)。不正なデータグラムは Datagram イベントを生成せず、Error イベントのみを生成する (接続クローズ前に不正なセッション ID がアプリへ渡らないようにする)
- 接続クローズ後の後始末も行う。`quic_connection.close(0x0108)` はローカル側のクローズであり、イベントを push しないため、`server.py` は CONNECTION_CLOSE 送出後に `_clients` からエントリを削除し (同一アドレスからの再接続をブロックしないため)、`client.py` は `_running = False` / `_connected = False` に更新して run() を終了させる
- 空データグラム・Quarter Stream ID の varint が途中で切れているデータグラムは、現状どおり黙って捨てる (接続クローズの対象としない)
- 仕様の MUST はストリーム経路 (WT データストリームのヘッダーのセッション ID) にも適用されるが、本 issue はデータグラム経路のみを対象とする (ストリーム経路の検証は対象外)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (セッション ID 検証とエラーイベント生成)、`src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` (ERROR ハンドラの追加と接続クローズ、`on_datagram` の docstring と DATAGRAM 分岐のコメント更新 (不正 ID は接続クローズとなり `on_datagram` に渡らないため))、テスト (既存の `test_datagram_negative_session_id_passed_through` の期待値更新を含む)、`CHANGES.md` ([FIX] エントリ追加)

## 完了条件

- 仕様逸脱ピアが巨大な Quarter Stream ID を持つデータグラムを送った場合、H3_ID_ERROR (0x0108) で接続が閉じられる (負のセッション ID になる 2^61 以上と、正のまま範囲超過になる 2^60 以上 2^61 未満の両方を検証する)
- 正常なセッション ID のデータグラムは従来どおり配送される (閉じたセッションの ID も含む)
- サーバー/クライアントの両方で、不正なセッション ID のデータグラム受信時に接続が閉じられる (クライアント側はサーバーから不正な Quarter Stream ID を持つデータグラムを送る構成で検証する。クライアント側の検証は高レベル `Client` を使うか、`_LowLevelClient` に DATAGRAM 分岐を追加する必要がある。接続クローズの確認は、0x0108 を検証可能な側 (サーバー側の `error_code()`) で行う)
- モックなしのテストで検証できる (既存の `test_datagram_negative_session_id_passed_through` の期待値を更新する)
