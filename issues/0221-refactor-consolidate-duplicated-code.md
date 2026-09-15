# 重複実装を共通ヘルパーへ集約する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-duplicated-code
- Polished: {YYYY-MM-DD}

## 目的

同一実装が複数箇所に存在し、仕様変更やバグ修正の影響範囲が広がっている。共通ヘルパーへ集約して保守性を高める。closed/0125 で URL パース・ヘッダー変換・セッション終了ガードなどの集約は完了しており、本 issue は残った重複を扱う。

## 現状

C++ 側:

- `is_valid_utf8` が `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h3.cpp` に完全一致の実装で存在する
- QUIC 可変長整数のエンコードとデコードが 4 系統ある。`src/bindings/quic.cpp` の `quic_varint_length` / `decode_varint` と、`src/bindings/webtransport_h2.cpp` の `H2Session::encode_varint` / `H2Session::decode_varint` が同一仕様 (RFC 9000 Section 16) を別実装しており、境界値の定数も書き分けられている
- WT_APPLICATION_ERROR の写像が C++ と Python に二重実装されている。`src/bindings/webtransport_h3.cpp` の `webtransport_code_to_http_code` 系と `src/webtransport/h3/_error_codes.py` が同じ式と同じ魔法数を持つ。片側だけ改訂されるとワイヤ値が食い違う

Python 側:

- `_timeout_seconds` が 6 箇所、`_send_pending` が 5 箇所にほぼ同一実装で存在する
- `_resolve_remote` が `src/webtransport/quic/client.py` / `h3/client.py` / `http3/client.py` の 3 箇所に同一実装で存在する
- DCID 索引を扱う `_refresh_dcid_index` / `_drop_dcid_index` / `_addr_of` が `src/webtransport/quic/server.py` / `h3/server.py` / `http3/server.py` に重複している
- 受信ループの `_receive` は 0209 で `src/webtransport/_common.py` の `recv_datagram` へ集約済みのため、本 issue の対象外とする

テスト側:

- `http3.Connection` 用の `_pump` が 6 ファイル、`_create_connection_pair` が 5 ファイルにほぼ同一実装で存在する。`tests/conftest.py` の `_pump` は `h3.Session` 専用のため流用できていない
- 自己署名証明書の生成が `tests/conftest.py` の `test_certificates` フィクスチャと `create_test_certificates` の 2 実装ある
- `_encode_capsule` が `tests/conftest.py` と `tests/test_webtransport_h3_close_session_message.py` に同一実装で存在する

## 設計方針

- C++ 側の共通化先は既存の `src/bindings/header_convert.h` を拡張するか、新たな共通ヘッダを設ける。名前は実態に合わせて一般化する
- Python 側の共通化先は `src/webtransport/_common.py` とする。既存の集約方針 (CHANGES.md の misc 節と 0209 の受信ループ集約) に従う
- テスト側の共通化先は `tests/conftest.py` とし、closed/0028 / 0081 / 0202 と同じ方針で進める
- 挙動を変えない純粋なリファクタリングとする。イベント列やワイヤ出力に差が出ないことをテストで確認する
- 一度にすべてを扱うと差分が大きくなるため、C++ の共通化・Python の共通化・テストの共通化でコミットを分ける

## 完了条件

- 上記の重複がそれぞれ 1 箇所になり、既存の全テストが通過する
- 共通化によって挙動が変わっていないことを、既存のテストと追加したテストで確認できる

## 解決方法

- `src/bindings/webtransport_h2.cpp` / `webtransport_h3.cpp` の `is_valid_utf8` を共通ヘッダへ移す
- `src/bindings/quic.cpp` と `src/bindings/webtransport_h2.cpp` の可変長整数の実装を共通ヘルパーへ統合する
- WT_APPLICATION_ERROR の写像は片方を正本にし、もう片方から呼ぶ形にする
- `src/webtransport/_common.py` に送信処理・タイマー期限・名前解決・DCID 索引のヘルパーを追加し、各層から呼ぶ
- `tests/conftest.py` に `http3.Connection` 用のポンプとペア生成、証明書生成の一本化、`_encode_capsule` を集約し、各テストファイルの複製を削除する
