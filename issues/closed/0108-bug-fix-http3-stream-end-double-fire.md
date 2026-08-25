# HTTP/3 高レベル層で on_stream_end が二重通知される問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-http3-stream-end-double-fire
- Polished: 2026-08-24

## 目的

ボディなしレスポンス (204 / 304 等) で、ヘッダー情報とストリームの FIN が同一の QUIC STREAM_DATA として届いた場合に、高レベル層の `on_stream_end` コールバックが 2 回呼ばれる問題を修正する。集計系アプリで重複カウントが発生する。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::end_headers_cb` は fin=1 のとき STREAM_END イベントを積む (ヘッダーだけでストリームが終わる終端検知)
- 高レベル層 `src/webtransport/http3/client.py` の `Client.run` は、 (a) HTTP/3 層の STREAM_END イベントと (b) 受信した QUIC FIN (`finished_streams`) の両方で `on_stream_end` を呼ぶ
- この 2 経路が同一の QUIC STREAM_DATA (ヘッダー + FIN) で両方発火し、`on_stream_end` が 2 回呼ばれる (RFC 9114 Section 4.1 で正当なワイヤパターン)。実ブラウザや他実装がこの形で送り得る
- 現行の高レベル `Server` 構成 (submit_response 直後に 2xx を flush し、その後の send_data(fin=True) は別フレーム) では再現しないため、再現するテストは Sans-IO でヘッダー + FIN を同一の書き出しにする注入構成が必要
- `tests/test_e2e_http3.py` の `test_stream_end_callback` はボディ付きレスポンスのみでこの経路を踏んでおらず、また単一発火 (`ended_stream_ids == [stream_id]`) を pin 留めしている

## 設計方針

- 高レベル層の `on_stream_end` 通知を QUIC FIN (`finished_streams`) の単一経路に統一する。QUIC FIN はボディの有無によらずストリーム終端の完全な検知手段である
- 低レベル (`end_headers_cb` の fin=1 による STREAM_END イベント) はそのまま残す (低レベル API の契約は変更しない。ヘッダー終端の終端検知として意味を持つ)。高レベル層の run() がこのイベントで `on_stream_end` を呼ぶのをやめるだけでよい
- サーバー側 (`src/webtransport/http3/server.py`) も同一の構造を持つ場合、対称に整理する (リクエストストリームの受信終端検知の単一化)
- 変更対象: `src/webtransport/http3/client.py` / `server.py` (on_stream_end 通知経路の単一化) / テスト (Sans-IO のヘッダー + FIN 注入構成) / CHANGES.md (## develop への [FIX])

## 完了条件

- ヘッダー + FIN が同一の QUIC STREAM_DATA で届くボディなしレスポンスでも、`on_stream_end` が 1 回だけ呼ばれる (ボディ付きレスポンスでの単一発火も維持される)
- `test_stream_end_callback` のボディ付き単一発火の pin 留めが維持される
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加され、全テストが通る

## 解決方法

- `src/webtransport/http3/client.py` の `Client.run` で、低レベル `STREAM_END` イベントによる `on_stream_end` 呼び出しを削除し、通知経路を受信 QUIC FIN (`finished_streams`) の単一経路に統一した。ヘッダーと FIN が同一の QUIC STREAM_DATA として届く場合 (RFC 9114 Section 4.1 のメッセージフレーミングと Section 6 のフレーム境界の独立性により正当なワイヤパターン)、両経路で通知すると 2 回呼ばれるため。低レベルの STREAM_END イベント (ヘッダー終端の終端検知) は低レベル API の契約としてそのまま維持する
- `server.py` は `on_stream_end` 相当の通知経路が存在しないため変更対象外 (設計方針の条件付き記述どおり)
- テスト: `tests/test_e2e_http3.py` の `test_stream_end_callback_bodyless_response` (ボディなし 204 の単一発火ピン) と `tests/test_http3_message_ext.py` の `test_http3_headers_fin_same_chunk_stream_end_once` (ヘッダー + FIN 同一チャンクで低レベルが STREAM_END を 1 回だけ積むことのピン)
