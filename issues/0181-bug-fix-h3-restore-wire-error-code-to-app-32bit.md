# WebTransport over HTTP/3 の受信側でワイヤ上のエラーコードを 32 bit アプリコードに復元して on_stream_reset に配信する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-restore-wire-error-code-to-app-32bit
- Polished: {YYYY-MM-DD}

## 目的

`deliver_stream_reset_error_code` は WT_APPLICATION_ERROR レンジのワイヤ値を 32 bit アプリコードに逆変換せず、そのままアプリへ配信する。draft-ietf-webtrans-http3-16 Section 4.4 は「WebTransport data streams のエラーコードは endpoint がアプリへ配信する際も unchanged で MUST」と書く一方、同節「アプリエラーは 32 bit (0x00000000 - 0xffffffff)」および Figure 4 に逆変換の疑似コードを載せている。ブラウザの WebTransport 実装は JS アプリへ 32 bit の値を配信するが、本ライブラリは wire の 6 バイト値 (例: アプリコード 42 なら 91141958510854) を配信するため、対向の JS アプリと本ライブラリの Python アプリでエラーコードで合意できない。相互運用性のバグ。

## 現状

- `src/webtransport/h3/_error_codes.py` の `deliver_stream_reset_error_code` はデータストリームでワイヤ値をそのまま返すのみで `http_code_to_webtransport_code` を呼ばない
- 呼び出しは `src/webtransport/h3/client.py` の `Client._process_quic_events` と `src/webtransport/h3/server.py` の `Server._process_quic_events`
- `_error_codes.py` の `http_code_to_webtransport_code` (逆変換) は実装済みだが library 内から呼ばれず、docstring も「参照実装として提供する」と自認
- 実験: アプリコード 42 を送ると対向の `on_stream_reset` は 91141958510854 を受け取る (ブラウザは 42 を配信する)
- CONNECT ストリームは非リマップで直接配信 (正しい)
- draft-16 Section 4.4 (refs 800-841 行): 「WebTransport application errors ... constrained to an unsigned 32-bit integer」「MUST remap ... into the error range reserved for WT_APPLICATION_ERROR」「delivered unchanged, both by intermediaries forwarding on the wire and by endpoints delivering to the application」の 3 つの要求がある
- 「unchanged」は「途中の intermediary で変わらない」意味と解釈でき、endpoint 配信時に 32 bit へ復元することは「アプリレイヤの値としては変わらない」と整合する

## 設計方針

- `deliver_stream_reset_error_code` を、`is_wt_application_error_code` が真ならば `http_code_to_webtransport_code` で 32 bit アプリコードに復元する形に変える
- レンジ外 (予約済みコードポイント含む) は現状どおり `None` を配信する
- CONNECT ストリームは現状どおりリマップしない (HTTP/3 エラーコード空間のまま配信)
- `on_stream_reset` の型契約 `error_code: int | None` は変更不要 (実装が既に None を返し得る)
- 既存 issue 0122 の項目に含まれる可能性があるが本 issue で独立して追跡する

## 完了条件

- アプリコード 42 を送信すると対向の `on_stream_reset` が 42 を受け取ること
- レンジ外のワイヤ値は None として配信されること
- CONNECT ストリームは wire コードのまま配信されること
- `tests/prop_webtransport_h3.py` に error code のワイヤ→アプリ復元 roundtrip PBT を追加すること
- 既存のテスト全 822 件が引き続き通過すること
