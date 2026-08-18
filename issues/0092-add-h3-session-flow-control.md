# WebTransport over HTTP/3 のセッションフロー制御を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-session-flow-control
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http3-16 Section 5 で定義されるセッションフロー制御 (WT_MAX_STREAMS / WT_MAX_DATA / WT_STREAMS_BLOCKED / WT_DATA_BLOCKED カプセルと SETTINGS_WT_INITIAL_MAX_STREAMS_UNI / BIDI / MAX_DATA) を実装し、同 Section の MUST を満たす。現状はフロー制御が未実装のため、複数セッション共有時のリソース枯渇防止が効かず、仕様の MUST 未達になっている。

## 現状

- `src/bindings/webtransport_h3.cpp` にフロー制御カプセルの送受信コードが存在しない。カプセル処理は WT_CLOSE_SESSION のみ (`recv_wt_close_session_cb` / `nghttp3_conn_close_wt_session`)
- 依存ライブラリ nghttp3 (webtransport ブランチ) も未実装:
  - `_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` の `nghttp3_conn_open_wt_data_stream` に `/* TODO Check session flow control */` が残っている
  - CONNECT ストリーム (WT 制御ストリーム) のデータは nghttp3 の `nghttp3_conn_read_wt_ctrl_stream` に直行し、WT_CLOSE_SESSION 以外のカプセルは `NGHTTP3_WT_CTRL_STREAM_STATE_IGN` で黙殺される (フロー制御カプセルをライブラリ側で拾う現行経路が存在しない)
  - `nghttp3_settings` 構造体 (`_deps/nghttp3/webtransport/source/lib/includes/nghttp3/nghttp3.h`) に SETTINGS_WT_INITIAL_MAX_STREAMS_UNI / BIDI / MAX_DATA に相当するフィールドがなく、SETTINGS 送出にも nghttp3 側の対応が必要
- SETTINGS_WT_INITIAL_MAX_STREAMS_UNI / BIDI / MAX_DATA (0x2b64 / 0x2b65 / 0x2b61) も送出していない
- その結果、以下の MUST が未達:
  - Section 5.1「共有を許すエンドポイントはフロー制御を有効化する MUST」(本ライブラリのサーバーは複数セッション共有を許容)
  - Section 5.1「フロー制御無効時はクライアントが同時 2 セッション以上を張らない MUST」
  - Section 5.6.2「ピアのストリーム数制限を超えてストリームを開かない MUST」(`H3Session::open_stream` にセッション内ストリーム数制限なし)
  - Section 5.6.4「受信データが WT_MAX_DATA を超えたら WT_FLOW_CONTROL_ERROR でセッションを閉じる MUST」
  - Section 5.4「WT_MAX_STREAM_DATA / WT_STREAM_DATA_BLOCKED 受信はセッションエラーとする MUST」(0x190B4D3E / 0x190B4D42 が黙殺される)
  - Section 5.6.2 / 5.6.3「Maximum Streams が 2^60 を超える受信は WT_FLOW_CONTROL_ERROR でセッションを閉じる MUST」、Section 5.6.2 / 5.6.4「非増加の WT_MAX_STREAMS / WT_MAX_DATA 受信は WT_FLOW_CONTROL_ERROR でセッションを閉じる MUST」

## 設計方針

- **nghttp3 への対応は不可避のため、フロー制御は nghttp3 側の補完として実装する**。CONNECT ストリームのデータは nghttp3 の `nghttp3_conn_read_wt_ctrl_stream` に直行し、`webtransport_h3.cpp` 単独で受信カプセルを拾う経路が存在しないため、ライブラリ側のみでの実装は成立しない。nghttp3 の webtransport ブランチ (deps.json で `branch: "webtransport"` 指定) にフロー制御カプセルの送受信・SETTINGS 送出・受信 SETTINGS の解釈を実装し、`src/bindings/webtransport_h3.cpp` はその API を呼ぶ。nghttp3 への変更は上流に PR を作成する想定とし、完了が間に合わない場合は deps.json の参照先を固定して進める (受け渡し経路は実装時に決定する)
- nghttp3 の `nghttp3_settings` (送出側) と受信側の `nghttp3_proto_settings` の両方に SETTINGS_WT_INITIAL_MAX_STREAMS_UNI / BIDI / MAX_DATA の項目を追加し、`H3SessionConfig` に対応する設定項目 (初期ストリーム数・初期データ量) を追加する。既定値は「フロー制御を有効化しない」= 0 とする (フロー制御を有効にしたければユーザーが非 0 値を設定する)。H2 実装 (`H2SessionConfig` の `wt_initial_max_data` 等) と同様の構成にする
- フロー制御カプセル (WT_MAX_STREAMS / WT_MAX_DATA / WT_STREAMS_BLOCKED / WT_DATA_BLOCKED) の送受信を実装し、セッションごとのストリーム数・データ量の制限を H3Session 側で管理する
- **WT_MAX_DATA のデータ量カウント** は仕様 (Section 5.4) どおり「ストリームヘッダー (ストリームタイプ / シグナル値 / セッション ID) と CONNECT ストリーム上のカプセルを除外した、Stream Body データの総量」とする。受信超過の検出は受信側エンドポイント (サーバー / クライアント双方) に適用する。リセット済みストリームは QUIC の final size からストリームヘッダー分を差し引いた値を累計する。final size は nghttp3 の `reset_stream_cb` (送信側リセット要求の通知であり final size を持たない) では取得できないため、final size を保持する ngtcp2 の `stream_reset_cb` (`src/bindings/quic.cpp` の `QuicConnection::stream_reset_cb`、`final_size` 引数を持つ) から H3Session 側へ渡す経路を実装時に追加して取得する
- **フロー制御の有効/無効判定** は Section 5.1 どおり「双方が非 0 の SETTINGS_WT_INITIAL_MAX_* を 1 つ以上送った場合に有効」とする。受信 SETTINGS の判定は `recv_settings2_cb` で行う (現状は no-op)。ピアが非 0 SETTINGS を送らなかった場合はフロー制御無効とみなし、次の処理を行う:
  - クライアントは同時 2 セッション以上を張らない (既存セッションがあれば新規 CONNECT を出さない)
  - サーバーは 2 個目以降の CONNECT を H3_REQUEST_REJECTED でリセットする
  - フロー制御カプセルを受信しても **無視する** (Section 5.1 の MUST「フロー制御無効時はフロー制御カプセルの受信を無視する」。2^60 超過・非増加の検出は有効時にのみ適用する)
  - ピア SETTINGS 未受信時 (受信前の CONNECT や 2 セッション目) はフロー制御無効扱いとする (判定タイミングの境界は無効側に倒す)
- 過剰 CONNECT の拒否は Section 5.1 の MUST どおり `H3_REQUEST_REJECTED` で CONNECT ストリームをリセットする (非 2xx HTTP 応答を返す `reject_session` とは機構が異なる)
- WT_FLOW_CONTROL_ERROR の値は 0x045d4487 (Section 9.5 の HTTP/3 Error Code Registration) を使い、セッションクローズは既存の `close_session` (WT_CLOSE_SESSION) で行う
- 実装する MUST の範囲は「Section 5 のうち **エンドポイントに適用される** MUST 全体」とする (issue 本文に列挙したものに加え、非増加カプセル・2^60 超過の検出・フロー制御無効時のカプセル無視を含む)。Section 5.2 のレート制限 (SHOULD) と 5.6.1 の intermediary MUST (ホップバイホップの中間転送) は対象外
- テストでは、フロー制御カプセルの送受信・制限超過時の WT_FLOW_CONTROL_ERROR によるセッション閉鎖・フロー制御無効時のカプセル無視を検証する

## 完了条件

- フロー制御カプセル (WT_MAX_STREAMS / WT_MAX_DATA / WT_STREAMS_BLOCKED / WT_DATA_BLOCKED) の送受信が実装され、セッションごとのストリーム数・データ量の制限が機能する
- SETTINGS_WT_INITIAL_MAX_STREAMS_UNI / BIDI / MAX_DATA が送出され、双方非 0 設定でフロー制御が有効化される
- 制限超過 (ストリーム数・データ量・2^60 超・非増加カプセル) で WT_FLOW_CONTROL_ERROR (0x045d4487) によるセッション閉鎖が発生する
- フロー制御無効時にクライアントが同時 2 セッションを張れない・サーバーが過剰 CONNECT を H3_REQUEST_REJECTED で拒否する・フロー制御カプセルを受信しても無視する
- WT_MAX_STREAM_DATA / WT_STREAM_DATA_BLOCKED 受信でセッションエラーになる
- 上記がテストで検証できる
