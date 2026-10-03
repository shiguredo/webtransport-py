# h2 のテストで RST_STREAM フレーム生成ヘルパが複数ファイルに重複しているのを集約する

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-rst-stream-frame-helper
- Polished: 2026-10-03

## 目的

生の HTTP/2 フレームを組み立てて RST_STREAM を注入・検出するテストヘルパ `_encode_rst_stream_frame` が、`tests/conftest.py` の共有定義と 2 つのテストファイルのローカル定義に重複している。0237 の 対象外 はこの重複を「0221 / 0239 の対象」と記載しているが、0221 の現状に挙げられたテスト側の重複 (`_pump` / `_create_connection_pair` / 証明書の生成 / `_encode_capsule`) にも、0239 の対象 (カプセル種別の定数) にも含まれていない。0239 と closed の 0241 は残る重複の集約を本 issue で追跡すると明記しており、この重複に着手する issue は本 issue のみである。

## 現状

- 定義は 3 箇所にある
  - `tests/conftest.py` の `_encode_rst_stream_frame(stream_id, error_code=0)` (0241 で追加済み)
  - `tests/test_webtransport_h2_recv_flow_control.py` のローカル定義 `_encode_rst_stream_frame(stream_id, error_code=0)`
  - `tests/test_webtransport_h2_reject_session.py` のローカル定義 `_encode_rst_stream_frame(stream_id, error_code)`
- 3 つは同じバイト列を返す。長さ 4 のフレームヘッダ (Type 0x03 / Flags 0x00)、`stream_id & 0x7FFFFFFF` の 4 バイト、エラーコードの 4 バイトという構成である (RFC 9113 Section 6.4 の RST_STREAM)
- 違いは既定引数の有無 (`error_code=0`) と docstring だけである。`tests/test_webtransport_h2_incomplete_capsule_payload.py` のローカル実装は 0241 で削除済みで、conftest の定義を import している
- 呼び出し側の用途も揃っている。注入 (`server.receive(frame)`) と、送信されたワイヤバイト列に RST_STREAM が含まれるかの検出 (`_encode_rst_stream_frame(...) in wire`) の 2 通り。conftest の定義を import しているファイルは 4 つあり、`tests/test_webtransport_h2_recv_flow_control.py` には既定引数へ依存する呼び出し (`_encode_rst_stream_frame(session_id)`。`test_h2_stream_reset_releases_unconsumed_recv_bytes`) が 1 件ある
- 0237 の 対象外 の記述は本 issue の現状認識と食い違っている。closed の 0237 は書き換えず、本 issue で追跡する

## 設計方針

- 定義は `tests/conftest.py` に残す (0221 / 0239 と同じ方針。定義自体は 0241 で追加済みのため、移動は不要)
- 既定引数は `error_code: int = 0` のままとする (conftest の既存定義に合わせる。ローカル定義側に既定引数へ依存する呼び出しが 1 件あるが、import 後も同じ挙動になる)
- docstring は conftest の 1 箇所にまとめ、注入と検出の両方の用途を持つことを書く。RFC 9113 Section 6.4 への参照もここに置く (現在の conftest の docstring は RFC 9113 Section 6.4 への参照と nghttp2 のフラグ挙動を持つが、用途の記述が無いため追記する)
- ローカル定義を持つ 2 ファイルは conftest の定義を import し、ローカル定義を削除する
- 生成されるバイト列は変えない。既存テストの通過で確認する
- 変更対象: `tests/conftest.py` (docstring の追記)、`tests/test_webtransport_h2_recv_flow_control.py`、`tests/test_webtransport_h2_reject_session.py`

## 完了条件

- `_encode_rst_stream_frame` の定義が `tests/conftest.py` の 1 箇所になっている (テストファイルにローカル定義が残っていない)
- ローカル定義を持っていた 2 ファイルが conftest の定義を import して使っている
- 生成されるバイト列が変わっていない (既存テストが通過する)
- 全テストが通過する
