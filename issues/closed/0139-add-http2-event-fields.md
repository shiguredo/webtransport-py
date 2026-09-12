# HTTP/2 の PING・WINDOW_UPDATE イベントに不足フィールドを追加する

- Created: 2026-09-03
- Completed: 2026-09-12
- Branch: feature/add-http2-event-fields
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 低レベル API (`http2.Connection`) の受信イベントから PING の opaque data・ACK と WINDOW_UPDATE の増分値を観測できるようにし、RTT 測定・疎通確認・フロー制御監視を可能にする。

## 現状

- **PING の opaque data がイベントに含まれず、ACK PING も観測できない**: `src/bindings/http2.cpp` の `on_frame_recv_callback` は ACK フラグなしの PING のみをイベント種別のみで通知し、8 バイトの opaque data (RFC 9113 Section 6.7) を渡さない。ACK フラグ付き PING はイベントにならない。さらに `Http2Connection::ping` は `nghttp2_submit_ping` に NULL を渡すため、送信側も opaque data を設定できない
- **WINDOW_UPDATE の増分値がイベントに含まれない**: 同コールバックの WINDOW_UPDATE イベントに RFC 9113 Section 6.9 (WINDOW_UPDATE) の Window Size Increment が含まれず、観測できない
- RFC 9113 は `refs/` に未収録のため、実装時に一次資料を確認すること

## 設計方針

- `src/bindings/http2.h` の `Http2Event` に次を追加し、`src/webtransport/http2/__init__.pyi` の `Event` に公開する。pyi は nanobind 生成物のため手編集せず、bindings 側の定義変更から再生成する (0133 の確立規約)
  - `opaque_data`: PING の 8 バイト。PING 以外のイベントでは空
  - `ack`: PING ACK の場合のみ true。PING 以外のイベントでは false
  - `window_size_increment`: WINDOW_UPDATE の増分値。WINDOW_UPDATE 以外のイベントでは 0
- `on_frame_recv_callback` の PING 分岐で `frame->ping.opaque_data` をコピーし、ACK フラグで `ack` を立てる (ACK PING もイベント化する)。WINDOW_UPDATE 分岐で `frame->window_update.window_size_increment` を設定する
- `Http2Connection::ping` に 8 バイトの opaque data 引数を追加する。省略時は従来通りゼロ 8 バイトを送る。長さ不正は `std::runtime_error` を送出する (bindings の既存規約)
- 0129 と同一ファイル (`src/bindings/http2.cpp`) を変更するため、並行着手する場合は順序調整または rebase 前提とする

## 完了条件

- 受信 PING イベントが 8 バイトの opaque data を持ち、ACK PING が `ack=true` で観測できる。`ping()` に渡した opaque data がピア側で観測され、ピアの ACK PING が同一データで観測されるテストがある
- WINDOW_UPDATE イベントが Window Size Increment を保持するテストがある
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (PING・WINDOW_UPDATE 項目を移管)
- `issues/0129-add-http2-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整

## 解決方法

`Http2Event` に 3 フィールドを追加し、`ping()` で opaque data を指定できるようにした。

- `src/bindings/http2.h` の `Http2Event` に `opaque_data` (PING の 8 バイト) / `ack` (PING ACK か) / `window_size_increment` (WINDOW_UPDATE の増分値) を追加した
- `src/bindings/http2.cpp` の `on_frame_recv_callback` を変更した。PING は ACK も含めてイベント化し、`frame->ping.opaque_data` の 8 バイトと ACK フラグを載せる (RFC 9113 Section 6.7)。WINDOW_UPDATE は `frame->window_update.window_size_increment` を載せる (RFC 9113 Section 6.9)
- `Http2Connection::ping` に 8 バイトの `opaque_data` 引数を追加した。未指定なら nghttp2 の既定どおりゼロ 8 バイトを送り、8 バイト以外は `std::runtime_error` を送出する
- nanobind の型キャストでは `bytes` を `std::vector<uint8_t>` に渡せない (nanobind の `seq_get` が `bytes` / `str` をシーケンスとして拒否する) ため、`ping` の binding は lambda で `nb::bytes` を受けて明示変換する形にした
- 型スタブは `make develop` で再生成し、`src/webtransport/http2/__init__.pyi` に `opaque_data` / `ack` / `window_size_increment` と `ping(opaque_data: bytes = b'')` が反映された
- テストを 5 本追加した。`test_http2_ping_opaque_data_roundtrip` (送信 opaque data のピア観測と ACK の同一データ観測) / `test_http2_ping_without_opaque_data_sends_zero_bytes` / `test_http2_ping_invalid_opaque_data_length_raises` / `test_http2_window_update_increment_observed` (受信ウィンドウ超過のボディ送出で再開放を観測) / `test_http2_window_update_event_fields_default_for_other_events`
- `skills/webtransport-py/SKILL.md` の `http2.Event` フィールド一覧と `ping` のシグネチャを更新した

`window_size_increment` の代入を外すと `test_http2_window_update_increment_observed` が失敗することを確認し、テストが実際に増分値を検証していることを確かめた。`uv run pytest tests/ --timeout=30` の 1069 件が全て通る。
