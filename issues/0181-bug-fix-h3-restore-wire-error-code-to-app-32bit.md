# WebTransport over HTTP/3 の受信側でワイヤ上のエラーコードを 32 bit アプリコードに復元して on_stream_reset に配信する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-restore-wire-error-code-to-app-32bit
- Polished: 2026-09-07

## 目的

`deliver_stream_reset_error_code` は WT_APPLICATION_ERROR レンジのワイヤ値を 32 bit アプリコードに逆変換せず、そのままアプリへ配信する。draft-ietf-webtrans-http3-16 Section 4.4 は送信側の MUST remap と 32 bit 制約 (0x00000000 - 0xffffffff) および Figure 4 の逆変換疑似コードを載せる一方、受信側は「The error code from a WebTransport stream reset MUST be delivered unchanged, both by intermediaries forwarding on the wire and by endpoints delivering to the application」と定める。closed issue 0095 は後者を根拠に「受信時は逆変換しない」と決定済みである。一方、ブラウザの参照実装 (Chromium の quiche) は受信 RESET_STREAM / STOP_SENDING をアプリへ渡す際にワイヤ値からアプリコードへ逆変換しており、W3C WebTransport 仕様 Section 13 もアプリ・ワイヤ間の双方向変換を定める。相互運用性のため 0095 の決定を再審し、受信時逆変換に変える。

## 現状

- `src/webtransport/h3/_error_codes.py` の `deliver_stream_reset_error_code` はデータストリームでワイヤ値をそのまま返すのみで `http_code_to_webtransport_code` を呼ばない
- 呼び出しは `src/webtransport/h3/client.py` の `Client._process_quic_events` と `src/webtransport/h3/server.py` の `Server._process_quic_events`
- `_error_codes.py` の `http_code_to_webtransport_code` (逆変換) は実装済みだが library 内から呼ばれず、docstring も「参照実装として提供する」と自認
- 実験手順 (Sans-IO の H3Session ペア。確立済みセッションで片側がアプリコード 42 でリセットし、対向の `on_stream_reset` 観測値を記録する): 現状は 91141958510854 が配信される方向であり、実行記録は未取得のため実装時に再測定する
- CONNECT ストリームは非リマップで直接配信 (正しい)
- draft-16 Section 4.4 (refs 800-856 行): 送信側 MUST remap、32 bit 制約、Figure 4 の逆変換疑似コード、受信側 unchanged 規定 (845-847 行)、レンジ外 SHOULD (854-855 行) がある
- closed issue 0095 は unchanged 規定を根拠に受信時無変換配信を決定済みであり、本 issue の提案は当該決定と真逆である
- ブラウザ証拠 (検証済み): Chromium の quiche 実装 (`WebTransportHttp3UnidirectionalStream::OnStreamReset` と `OnStopSending`) は受信したワイヤ値を `Http3ErrorToWebTransportOrDefault` でアプリコードに逆変換してからアプリ visitor へ渡す。W3C WebTransport 仕様 Section 13 もアプリ・ワイヤ間の双方向変換 (and vice versa) を定める。以上により 0095 の再審根拠は揃っており、本 issue は再審可として進める

## 設計方針

- 再審は可と判断済みである (ブラウザ証拠を現状に添付済み)。以下に進む
- `deliver_stream_reset_error_code` を、`is_wt_application_error_code` が真ならば `http_code_to_webtransport_code` で 32 bit アプリコードに復元する形に変える
- レンジ外 (予約済みコードポイント含む) は現状どおり `None` を配信する
- CONNECT ストリームは現状どおりリマップしない (HTTP/3 エラーコード空間のまま配信)
- `on_stream_reset` の型契約 `error_code: int | None` は変更不要 (実装が既に None を返し得る)
- STOP_SENDING 受信通知は未実装のため本 issue の対象外とする
- 変更対象は `src/webtransport/h3/_error_codes.py` のみとし、docstring 3 箇所 (client.py / server.py の `on_stream_reset` 説明と `_error_codes.py` の参照実装注記) の更新と `CHANGES.md` の FIX エントリを付ける。既存 e2e 2 件と単体 1 件のワイヤコード期待値は 32 bit アプリコードに更新する

## 依存関係

- closed issue 0095 (受信時無変換配信の決定) と真逆の方針であり、0095 の再審可として本 issue で置き換える
- open issue 0182 (型契約) とは `int | None` 維持で整合し、0187 (PBT 計画) とは wire→app 方向の配信確認という住み分けである

## 完了条件

- 再審可の判断済みであること (ブラウザ証拠は現状に添付済み)
- アプリコード 42 を送信すると対向の `on_stream_reset` が 42 を受け取ること
- レンジ外のワイヤ値は None として配信されること
- CONNECT ストリームは wire コードのまま配信されること
- `tests/prop_webtransport_h3.py` に error code のワイヤ→アプリ復元 roundtrip PBT を追加し、既存 e2e 2 件と単体 1 件の期待値を更新すること
- 既存のテスト全 834 件が引き続き通過すること
