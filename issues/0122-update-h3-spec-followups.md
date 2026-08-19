# WebTransport over HTTP/3 の仕様追従の残りを対応する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/update-h3-spec-followups
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 への追従のうち、細かい仕様逸脱・観測性の欠落として残っている項目をまとめて対応する。

## 現状

- **2xx 非 200 応答で SESSION_READY が発火しない**: `src/bindings/webtransport_h3.cpp` の `H3Session::end_headers_cb` は `status == "200"` のみ SESSION_READY を発火する。draft-16 Section 3.2 は「2xx series status code」で確立と規定しており、201 等の 2xx ではアプリに確立通知が来ない (データ交換は機能するが通知だけ欠落)
- **WT_DRAIN_SESSION 受信経路がない**: draft-16 Section 4.7 の WT_DRAIN_SESSION (0x78ae) は nghttp3 が未知カプセルとして黙殺し、アプリへの通知がない (H2 側には SessionDraining イベントがある)
- **バッファリング上限がない**: Section 4.6「endpoints MUST limit the number of buffered streams and datagrams」に対し、受理前ストリームは nghttp3 が WT_SESSION_BLOCKED で無制限にバッファし、送信待ちデータグラムキューも無制限
- **connect() にタイムアウトがない**: `src/webtransport/h3/client.py` の `Client.connect` はハンドシェイク完了待ちが無制限 (QUIC 層は timeout 引数を持つ)
- **SETTINGS 受信判定が stream_id ハードコード**: `src/webtransport/h3/client.py` の `Client.connect` は「制御ストリーム (stream_id=3) にデータが届いたら SETTINGS 受信とみなす」実装で、SETTINGS フレームの処理完了を確認していない
- **close() がピアの CONNECT ストリームクローズを待たない**: `src/webtransport/h3/client.py` の `Client.close` は WT_CLOSE_SESSION 送出直後に QUIC 接続を閉じる。draft-16 Section 6 の「ピアが全 CONNECT ストリームを閉じるまで CONNECTION_CLOSE を送るのを待つ SHOULD (WT_CLOSE_SESSION のアプリエラー情報配信を保証するため)」に反する (H2 側は half-close 完了をポーリングする)

## 設計方針

- 2xx 非 200 で SESSION_READY を発火させる
- WT_DRAIN_SESSION 受信をアプリへ通知する経路を追加する
- バッファリング上限と超過時の扱い (WT_BUFFERED_STREAM_REJECTED / データグラム破棄) を実装する
- connect() にタイムアウトを追加する
- SETTINGS 受信判定を `h3.Session` 側の SETTINGS 受信の直接観測に置き換える
- close() でピアの CONNECT ストリームクローズを待つ (H2 と対称の実装)

## 完了条件

- 上記 6 項目がすべて対応され、それぞれのテストがある
