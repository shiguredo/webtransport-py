# WebTransport over HTTP/3 の受信側でワイヤ上のエラーコードを 32 bit アプリコードに復元して on_stream_reset に配信する

- Created: 2026-09-07
- Completed: 2026-09-12
- Branch: feature/fix-h3-restore-wire-error-code-to-app-32bit
- Polished: 2026-09-09

## 目的

`deliver_stream_reset_error_code` は WT_APPLICATION_ERROR レンジのワイヤ値を 32 bit アプリコードに逆変換せず、そのままアプリへ配信する。draft-ietf-webtrans-http3-16 Section 4.4 はアプリコードを unsigned 32-bit (0x00000000 - 0xffffffff) と定め、送信側の MUST remap と Figure 4 の逆変換疑似コードを載せる。受信側は「The error code from a WebTransport stream reset MUST be delivered unchanged, both by intermediaries forwarding on the wire and by endpoints delivering to the application」と定めるが、これは字義どおりにはワイヤ値無変換とも読める。一方 draft-ietf-webtrans-overview-13 (refs 585-610 行) はアプリへ渡すイベントのエラーコードを「An unsigned 32-bit error code from the peer might be available」と定めており、約 47 bit の WT_APPLICATION_ERROR ワイヤ値を無変換で配信するとこの 32 bit 契約に反する。そこで unchanged は「アプリコードが end-to-end で保存されること (中間ノードはワイヤフレームを改変しないこと)」を指すと解釈する。closed issue 0095 は unchanged をワイヤ値無変換の根拠にしたが、32 bit 契約との矛盾を考慮していない。ブラウザの参照実装 (Chromium の quiche) は受信 RESET_STREAM / STOP_SENDING をアプリへ渡す際にワイヤ値からアプリコードへ逆変換しており、W3C WebTransport 仕様 Section 13 (non-normative) もアプリ・ワイヤ間の双方向変換を定める。相互運用性と 32 bit 契約のため 0095 の決定を再審し、受信時逆変換に変える。

## 現状

- `src/webtransport/h3/_error_codes.py` の `deliver_stream_reset_error_code` はデータストリームでワイヤ値をそのまま返すのみで `http_code_to_webtransport_code` を呼ばない
- 呼び出しは `src/webtransport/h3/client.py` の `Client._process_quic_events` と `src/webtransport/h3/server.py` の `Server._process_quic_events`
- `_error_codes.py` の `http_code_to_webtransport_code` (逆変換) は実装済みだが library 内から呼ばれず、docstring も「参照実装として提供する」と自認
- 実験手順 (高レベル `h3.Client` / `h3.Server` ペア。確立済みセッションで片側がアプリコード 42 でリセットし、対向の `on_stream_reset` 観測値を記録する。`on_stream_reset` は高レベル層のコールバックであり `H3Session` には無い): 現状は 91141958510854 が配信される方向であり、実行記録は未取得のため実装時に再測定する
- CONNECT ストリームは非リマップで直接配信 (正しい)
- draft-16 Section 4.4 (refs 800-856 行): 送信側 MUST remap、アプリコードの unsigned 32-bit 制約、Figure 4 の逆変換疑似コード、受信側 unchanged 規定 (845-847 行)、レンジ外は SHOULD でアプリエラーコードなし (854-855 行)
- draft-overview-13 (refs 585-610 行): アプリへ渡す「send side aborted」/「receive side aborted」のエラーコードは unsigned 32-bit
- closed issue 0095 は unchanged 規定 (845-847 行) を根拠に受信時無変換配信を決定済みであり、本 issue の提案は当該決定と真逆である
- ブラウザ証拠: Chromium の quiche 実装 (`WebTransportHttp3UnidirectionalStream::OnStreamReset` と `OnStopSending`) は受信したワイヤ値を `Http3ErrorToWebTransportOrDefault` でアプリコードに逆変換してからアプリ visitor へ渡す。W3C WebTransport 仕様 Section 13 は non-normative だがアプリ・ワイヤ間の双方向変換 (and vice versa) を定める

## 設計方針

- 0095 の「受信時無変換」を再審し、本 issue で受信時逆変換に置き換える。根拠は draft-overview-13 の 32 bit 契約とブラウザ実装であり、draft-16 の unchanged はアプリコードの end-to-end 保存を指すと解釈する
- `deliver_stream_reset_error_code` を、`is_wt_application_error_code` が真ならば `http_code_to_webtransport_code` で 32 bit アプリコードに復元する形に変える
- レンジ外 (予約済みコードポイント含む) は現状どおり `None` を配信する (draft-16 の SHOULD に従う。Chromium は 0 に落とすが本実装は `None` とする)
- CONNECT ストリームは現状どおりリマップしない (HTTP/3 エラーコード空間のまま配信)
- `on_stream_reset` の型契約 `error_code: int | None` は変更不要 (実装が既に `None` を返し得る)
- STOP_SENDING 受信通知は未実装 (open issue 0162) のため本 issue の対象外とする。0162 実装後は h3 層の STOP_SENDING 経路も同じ `deliver_stream_reset_error_code` を通す必要がある
- ロジック変更は `src/webtransport/h3/_error_codes.py` のみ。付随して docstring 4 箇所 (`client.py` / `server.py` の `on_stream_reset` 説明、`_error_codes.py` の `deliver_stream_reset_error_code` と `http_code_to_webtransport_code`) を更新し、テスト期待値と `CHANGES.md` の FIX エントリを付ける。既存 e2e 2 件と単体 1 件のワイヤコード期待値は 32 bit アプリコードに更新する

## 依存関係

- closed issue 0095 (受信時無変換配信の決定) と真逆の方針であり、0095 の再審可として本 issue で置き換える
- open issue 0182 (型契約) とは `int | None` 維持で整合する
- open issue 0187 (PBT 計画) は本 issue の roundtrip を回帰ピンとして使う。本 issue が通常 PBT (`tests/prop_webtransport_h3.py`) の roundtrip を追加し、0187 はステートフル PBT 側で回帰ピンを持つ (同じ roundtrip を二重実装しない)
- open issue 0162 (QUIC の STOP_SENDING 伝播) が実装されたら、h3 層の STOP_SENDING 経路も同じ逆変換を通す必要がある

## 完了条件

- アプリコード 42 を送信すると対向の `on_stream_reset` が 42 を受け取ること
- レンジ外のワイヤ値は `None` として配信されること
- CONNECT ストリームは wire コードのまま配信されること
- `tests/prop_webtransport_h3.py` に error code のワイヤ→アプリ復元 roundtrip PBT を追加し、既存 e2e 2 件と単体 1 件の期待値を更新すること
- 既存のテスト全 976 件が引き続き通過すること

## 解決方法

- `src/webtransport/h3/_error_codes.py` の `deliver_stream_reset_error_code` を、データストリームでは `is_wt_application_error_code` が真の場合に `http_code_to_webtransport_code` で unsigned 32-bit のアプリコードへ逆変換して返す形に変更した (draft-16 Section 4.4 の unchanged はアプリコードの end-to-end 保存を指すと解釈する)
- レンジ外、またはレンジ内の予約済みコードポイントは従来どおり `None` を配信し、CONNECT ストリームは非リマップのままとした
- `http_code_to_webtransport_code` の docstring を受信配信でも使う旨に更新し、`client.py` / `server.py` の `on_stream_reset` docstring もデータストリームは復元値、CONNECT は HTTP/3 コード空间のまま渡す旨に更新した
- `tests/test_webtransport_h3_error_code_remap.py` の配信テストをアプリコードへの復元・予約済み `None`・上端 / 下端 / 上側レンジ外の境界・CONNECT のレンジ内非リマップまで拡張した
- `tests/test_e2e_webtransport_h3.py` の受信期待値をワイヤコードからアプリコード (0x01 / 0x02) に更新した
- `tests/prop_webtransport_h3.py` にワイヤ→アプリ復元の roundtrip PBT を追加した
- `CHANGES.md` の develop にデータストリームリセットの受信復元の FIX エントリを追加した
- 全 1023 テストが通過することを確認した
