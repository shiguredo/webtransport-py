# テスト用の RST_STREAM フレーム生成ヘルパが 3 ファイルに重複している

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-rst-stream-frame-helper
- Polished: {YYYY-MM-DD}

## 目的

生の HTTP/2 フレームを組み立てて RST_STREAM を注入・検出するテストヘルパ `_encode_rst_stream_frame` が 3 ファイルに重複している。0237 の 対象外 はこの重複を「0221 / 0239 の対象」と記載しているが、0221 の現状に挙げられたテスト側の重複 (`_pump` / `_create_connection_pair` / 証明書の生成 / `_encode_capsule`) にも、0239 の対象 (カプセル種別の定数) にも含まれておらず、追跡されていない。

## 現状

- 同じ実装が 3 ファイルにある
  - `tests/test_webtransport_h2_incomplete_capsule_payload.py` の `_encode_rst_stream_frame(stream_id, error_code)`
  - `tests/test_webtransport_h2_recv_flow_control.py` の `_encode_rst_stream_frame(stream_id, error_code=0)`
  - `tests/test_webtransport_h2_reject_session.py` の `_encode_rst_stream_frame(stream_id, error_code)`
- 3 つは同じバイト列を返す。長さ 4 のフレームヘッダ (Type 0x03 / Flags 0x00)、`stream_id & 0x7FFFFFFF` の 4 バイト、エラーコードの 4 バイトという構成である (RFC 9113 Section 6.4 の RST_STREAM)
- 違いは既定引数の有無 (`error_code=0`) と docstring だけである
- 呼び出し側の用途も揃っている。注入 (`server.receive(frame)` / `client.receive(frame)`) と、送信されたワイヤバイト列に RST_STREAM が含まれるかの検出 (`_encode_rst_stream_frame(...) in wire`) の 2 通り
- 0237 の 対象外 の記述は本 issue の現状認識と食い違っている。closed の 0237 は書き換えず、本 issue で追跡する

## 設計方針

- 集約先は `tests/conftest.py` とする。0221 / 0239 と同じ方針である
- 既定引数は `error_code: int = 0` とする (3 ファイルの呼び出しはすべて実引数を渡しているため、既定値の有無は挙動に影響しない)
- docstring は 1 箇所にまとめ、注入と検出の両方の用途を持つことを書く。RFC 9113 Section 6.4 への参照もここに置く
- 生成されるバイト列は変えない。既存テストの通過で確認する
- 変更対象: `tests/conftest.py`、`tests/test_webtransport_h2_incomplete_capsule_payload.py`、`tests/test_webtransport_h2_recv_flow_control.py`、`tests/test_webtransport_h2_reject_session.py`

## 完了条件

- `_encode_rst_stream_frame` の定義が 1 箇所になっている
- 3 ファイルが conftest の定義を import して使っている
- 生成されるバイト列が変わっていない (3 ファイルのテストが通過する)
- 全テストが通過する
