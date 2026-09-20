# テスト用の部分 HEADERS バイト列が 2 ファイルに重複している

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-partial-headers-frame
- Polished: {YYYY-MM-DD}

## 目的

「HEADERS フレームの Length を実際のペイロードより大きく宣言した不完全なヘッダーブロック」を作るバイト列が `tests/test_http3.py` と `tests/test_e2e_http3_peer_reset.py` に同じ内容で重複している。片方だけを変更すると 2 ファイルのテストが別の入力を検証することになり、レビュー時の突き合わせコストと追従漏れの余地が生じる。0221 はテスト側の重複として `_pump` / `_create_connection_pair` / 証明書の生成 / `_encode_capsule` を扱うが、このバイト列は対象に入っていない。

## 現状

- `tests/test_http3.py` の `_inject_partial_headers(conn, stream_id)` は `bytes([0x01]) + _encode_varint(100) + b"\x00\x00"` を組み立て、`conn.receive_stream_data(stream_id, frame, False)` へ渡す。Length を 100 と宣言し、実際のペイロードは 2 バイトである
- `tests/test_e2e_http3_peer_reset.py` の `_PARTIAL_HEADERS` は同じバイト列を定数として持ち、`peer.send_stream_data(stream_id, _PARTIAL_HEADERS, fin=False)` と `peer_connection.send_stream_data(stream_id, _PARTIAL_HEADERS, False)` の 2 箇所で使う
- どちらのファイルも `tests/conftest.py` の `_encode_varint` を import しており、バイト列の組み立て方だけが重複している
- 「Length を 100 と宣言し、実際のペイロードは 2 バイト」という理由のコメントも両ファイルに別々に書かれている
- 用途が異なる (片方は Sans-I/O の `receive_stream_data` への注入、もう片方は実 QUIC ストリームへの送出) ため、単純な定数の共通化だけでは済まない可能性がある

## 設計方針

- 集約先は `tests/conftest.py` とする。0221 / 0239 と同じ方針である
- 置くのはバイト列そのもの (部分 HEADERS フレームの定数) とし、それをどう渡すかは利用側に残す。`tests/test_http3.py` の `_inject_partial_headers` は `conn` と `stream_id` を受け取る形を維持し、内部で共通のバイト列を使う。`tests/test_e2e_http3_peer_reset.py` の `_PARTIAL_HEADERS` は削除して共通の定義を参照する
- 「なぜ部分的な HEADERS を送るのか」(受信途中のヘッダーブロックを作るため) の説明は 1 箇所 (conftest) に置き、利用側のコメントは用途に絞る
- 生成されるバイト列は変えない。既存テストの通過で確認する
- 変更対象: `tests/conftest.py`、`tests/test_http3.py`、`tests/test_e2e_http3_peer_reset.py`

## 完了条件

- 部分 HEADERS のバイト列の定義が 1 箇所になっている
- `tests/test_http3.py` と `tests/test_e2e_http3_peer_reset.py` が同じ定義を使っている
- 生成されるバイト列が変わっていない (両ファイルのテストが通過する)
- 全テストが通過する
