# 受理前 WT_CLOSE_SESSION 送出テストヘルパーを conftest.py に集約する

- Created: 2026-08-15
- Completed: 2026-09-12
- Branch: feature/refactor-consolidate-test-helpers
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_webtransport_h3_pre_accept_fin.py` に定義された `_send_pre_accept_wt_close_session` (クライアントが受理前に WT_CLOSE_SESSION を送出して取り出すヘルパー) が、`tests/test_webtransport_h3_server_reject_session.py` に同一コードで複製された。同一のヘルパーが 2 箇所に存在すると、nghttp3 の WT_CLOSE_SESSION 送出時の FIN 付与挙動が変わった場合などの修正が 2 箇所必要になり、修正漏れのリスクになる。conftest.py に集約して 1 箇所にまとめる。

## 現状

- `tests/test_webtransport_h3_pre_accept_fin.py` の `_send_pre_accept_wt_close_session`
- `tests/test_webtransport_h3_server_reject_session.py` の `_send_pre_accept_wt_close_session`
- 2 つのヘルパーは close_session → get_streams_to_send でカプセルを取り出す処理と assert 3 連 (送出データが 1 件のみ・CONNECT ストリーム宛・FIN 付与) が完全に同一

## 設計方針

- 既存の集約方針 (CHANGES.md の「WebTransport over HTTP/3 テストの接続ヘルパーを conftest.py に集約する」「テストのイベント取り出しヘルパー _drain_events を conftest.py に集約する」) に従い、`tests/conftest.py` に移動する
- テスト本体の挙動は変えない純粋なリファクタリングとする

## 完了条件

- `tests/conftest.py` に `_send_pre_accept_wt_close_session` が 1 箇所だけ存在する
- `tests/test_webtransport_h3_pre_accept_fin.py` と `tests/test_webtransport_h3_server_reject_session.py` の複製ヘルパーが削除され、conftest.py からの import に切り替わる
- 全テストが通る

## 解決方法

`_send_pre_accept_wt_close_session` を `tests/conftest.py` の `_pump` の直後に集約し、2 ファイルの複製を削除した。

- `tests/conftest.py` に定義を 1 箇所だけ置き、docstring は両ファイルの記述を統合した (受理前カプセルが nghttp3 の inq にバッファされる仕様と、`get_streams_to_send` の 1 件性が `_setup_connect` の CONNECT ヘッダー書き出しに依存することの両方を保持)
- `tests/test_webtransport_h3_pre_accept_fin.py`: 複製ヘルパーを削除し、`conftest` からの import に切り替えた (import は 1 行が長くなるため括弧付きに整形)
- `tests/test_webtransport_h3_server_reject_session.py`: 同様に複製ヘルパーを削除し、既存の `conftest` import ブロックに追加した
- assert 3 連 (送出データが 1 件のみ・CONNECT ストリーム宛・FIN 付与) とテスト本体の挙動は変更していない

`uv run pytest tests/ --timeout=30` の 1058 件が全て通ることを確認した。
