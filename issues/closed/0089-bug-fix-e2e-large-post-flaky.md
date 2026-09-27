# e2e テスト test_large_post_body の flaky を修正する

- Created: 2026-08-15
- Completed: 2026-09-28
- Branch: feature/fix-e2e-large-post-flaky
- Polished: 2026-09-28

## 目的

`tests/test_e2e_http3.py` の `test_large_post_body` の flaky を調査し、修正する。テスト全体の実行で確実に通る状態にし、CI の信頼性を高める。

## 現状

- 2026-08-15 にローカル (macOS) で `uv run pytest tests/ -v --timeout=30` を実行した際、`test_large_post_body` が 1 回失敗した。受信データの内容不一致で、pytest の bytes 比較の失敗出力から不一致位置 (12694 バイト目) が観察された。失敗ログは保存されておらず、どの assert (サーバー受信側・クライアント受信側) で失敗したかも未確認
- その後、単体 9 回・全体テスト 3 回 (develop ブランチ)・全体テストの再実行 1 回では再現しない (観察は 1 回のみ)
- テストは QUIC 経由で 32 KiB のデータを送受信する実通信 E2E で、データは `bytes((index % 256) for index in range(32 * 1024))` の周期 256 のパターン。assert は 2 箇所 (サーバーが受信したボディ・クライアントが受信したエコー)
- **同じ症状は closed 0144 (2026-09-06 完了) が決定的に再現し、修正済みである**。0144 は CI (run 33970064230 の `test_macos 3.14t` ジョブ) で `tests/test_e2e_webtransport_h3.py` の `test_large_stream_payload` が `index 16923` で `got=0xa1 want=0x1b`、長さ 32768 のまま不一致になったことを受けて起票され、`QuicConnection::send()` が書き出し時に送信バッファを `erase()` で解放していた (ngtcp2 の保持契約違反) ことを `acked_stream_data_offset_cb` による受信確認ベースの解放に変えて修正した。回帰は `tests/test_quic_stream_retransmit.py` (Sans-IO のロス注入) として追加されている
- 0144 は「0089 と同症状の可能性があり、0089 の再現条件として参照する」と自ら記録している。0089 は 0144 の起票前 (2026-08-15) の issue であり、2026-09-28 の reopened 時に 0144 の知見を取り込んでいなかった
- 観察された不一致位置 12694 は 256 の倍数ではないため、周期 256 のパターンでも検出できる位置である。0144 の修正前はこの症状が決定的に再現しており、0089 の観察は 0144 が修正した経路で説明できる
- 検出限界は 3 ファイルに残っている: `tests/test_e2e_http3.py` の `test_large_post_body`、`tests/test_e2e_webtransport_h3.py` の `test_large_stream_payload` (0144 が CI で失敗を観測したテスト)、`tests/test_quic_stream_retransmit.py` の `PAYLOAD`。いずれも周期 256 のため、256 バイトの倍数のずれ (256・512・…) をバイト比較で検出できない
- 損失を注入できる実ソケット E2E のハーネスは既存である: `tests/lossy_relay.py` の `LossyRelay` (closed 0054 で新設)。クライアントとサーバーの間に挟む 1 対 1 の UDP リレーで、`drop_rule(packet: LossyRelayPacket) -> bool` で方向 (`direction` は "c2s" / "s2c") と方向別の通し番号 (`index`) を使った決定的な間引きができ、`dropped` / `forwarded` で結果を観測できる。`tests/test_e2e_quic_advanced.py` の `test_handshake_completes_with_initial_packet_loss` が使用例
- 2026-09-28 の RED / GREEN 実測: `src/bindings/quic.cpp` の保持分を書き出し時に解放する 0144 修正前の挙動へ一時的に戻して rebuild すると、`test_large_post_body_with_datagram_loss` の 3 通りと `tests/test_quic_stream_retransmit.py` の 3 通りが失敗した (RED)。損失注入テストの失敗出力は「サーバーが受信したボディ が一致しません: 長さ expected=32768 actual=32768 最初の不一致位置=2254 expected[2246:2278]=… actual[2246:2278]=…」で、どちらの受信側か・不一致位置・該当範囲が出力から分かる。修正を戻すと損失注入テスト 3 通り (ドロップ数は [3] が c2s 29 / s2c 27、[5] が 13 / 13、[8] が 7 / 7) と 0144 の回帰を含む関連テストが全通過する (GREEN)

## 設計方針

- 調査の主眼を「0144 の修正後の再発有無の確認」に置く。0144 は同じ送出経路の別バグ (再送バッファの早期解放) を特定・修正し、0034 の WRITE_MORE 契約も修正済みであり、QUIC 層単体で再現することから H3 層は無関係と記録している。同一経路の再探索は重複になるため行わず、0144 の修正が入った現行 develop で、同じ症状 (損失下の再送でデータが壊れる) が実ソケット E2E でも再発しないことを確認する
- 検出限界の解消: 3 ファイルの周期 256 のパターンを、周期 65536 のパターン `(index * 137 + index // 256) % 256` (32 KiB では反復しない) に置き換える。生成は `tests/conftest.py` の `_large_binary_payload()` に集約し、各テストはこれを import する。周期 256 のパターンは 256 バイトの倍数のずれを原理的に検出できない (256・512・… が盲点)。周期を 65536 に伸ばしても、比較長が短い大きなずれでは偶然一致が起こり得るため、検出保証は「256 バイトの倍数のずれを検出できる」までとする
- assert の強化: `tests/test_e2e_http3.py` の `test_large_post_body` の 2 箇所を `_assert_payload_matches(actual, expected, label)` に置き換え、不一致時にどちらの受信側か (label)・長さ・最初の不一致位置・期待値と実値の該当範囲を出力する。この出力内容は単体テスト (`test_assert_payload_matches_reports_mismatch_details`) で直接検証する
- 損失注入は既存の `LossyRelay` を再利用する (新規実装しない)。`LossyRelay(server_addr=("127.0.0.1", server.actual_port), drop_rule=...)` を作り、`Client(host="127.0.0.1", port=relay.actual_port)` で接続する。ドロップ規則は方向別の通し番号による決定的な間引き (`packet.index % drop_mod == drop_mod - 1`) とし、`drop_mod` を 3・5・8 の 3 通り回す
- 損失注入が作用したことを `relay.dropped["c2s"]` と `relay.dropped["s2c"]` が 0 でないことで確認する (0 件なら再送経路を通っておらず、何も検証していない)。注入なしの対照は `test_large_post_body` とする
- 変更対象: `tests/conftest.py` (`_large_binary_payload` の追加)、`tests/test_e2e_http3.py` (assert 強化・ペイロード差し替え・損失注入テストの追加)、`tests/test_e2e_webtransport_h3.py` と `tests/test_quic_stream_retransmit.py` (ペイロードの差し替え)。プロダクトコード (`src/` 配下) は変更しない (0144 の修正で足りているかの確認が本 issue の目的である)。`CHANGES.md` は変更しない (CODEBASE.md の指示)
- 対象外: `QuicConnection::send()` の再送バッファ管理 (closed 0144 で修正済み)、0034 の WRITE_MORE 契約 (closed 0034 で修正済み)、`LossyRelay` 自体の機能追加

## 完了条件

- `test_large_post_body` の assert が `_assert_payload_matches` になり、不一致時に label・長さ・最初の不一致位置・期待値と実値の該当範囲を出力すること。出力内容が単体テストで検証されていること
- 3 ファイルのペイロードが周期 65536 のパターン (`_large_binary_payload()`) になり、256 バイトの倍数のずれを検出できること
- 実ソケット E2E の損失注入テスト (`test_large_post_body_with_datagram_loss`) が `drop_mod` 3 通りで通過し、各実行で `relay.dropped` が双方向とも 0 でないこと (注入が作用したことの確認)。注入なしの対照 (`test_large_post_body`) も通過すること
- RED と GREEN の実測: 0144 の修正前の実装 (書き出し時に送信バッファを解放する実装) で損失注入テストが失敗することを確認できれば、その出力 (どちらの assert か・不一致位置・該当範囲) を記録する。再現しない場合は「0144 の修正後の develop では再発しない」ことを、実行回数・ドロップ数とあわせて `## 解決方法` に記録する (closed 0034 の先例に倣う)
- 全テストが通過すること

## reopened にした理由

- pending にした理由の「再現条件の解明に低負荷な方法 (パケット喪失の強制等) が必要であり、方針の見直しを要する」に従い、方針を見直して再開する。作業を 2 つに分ける: (1) 検出限界の解消 (assert の強化とテストデータの周期見直し)、(2) 実ソケット E2E での損失注入による 0144 修正の確認。(1) を先に実施することで、再現した場合の切り分けが可能になる
- 完了条件から「全体テスト 10 回連続 + 単体 50 回連続で失敗しない」を外した。再現率が極めて低く (単体 300 回で再現 0 回の記録がある)、回数を増やすだけの検証は CI 並みの負荷を要するわりに得られる情報が少ないため、再現手段の確立と観測の質 (不一致の完全な出力) を優先する
- `CHANGES.md` を変更対象から外した (CODEBASE.md の指示)
- 2026-09-28 の再開時に、本 issue の前提を closed 0144 の知見に合わせて書き直した。原因の調査 (0034 の再検証・QUIC / HTTP/3 層の探索) は 0144 が完了済みであり、本 issue の残作業は「検出限界の解消」と「0144 の修正後の再発有無の実ソケット E2E での確認」である

## pending にする理由

- 再現試行のテスト負荷が高く (全体テストの繰り返し実行が CI 並みの負荷になる)、実行を中断した
- 調査 (0034 の修正対象である `QuicConnection::send()` の再検証・QUIC / HTTP/3 層のデータパス・高レベル API) では明確なバグを特定できなかった (その後の 0144 が同経路を特定・修正した)
- 単体 50 回の再現試行で 1 回再現した (18 回目) が詳細が未取得で、その後の 300 回では再現しなかった (再現率は極めて低い)
- 再現条件の解明に低負荷の方法 (パケット喪失の強制等) が必要であり、方針の見直しを要するため pending とする。再現手順が確定した時点で reopened する

## 解決方法

- 検出限界の解消: `tests/conftest.py` に `_large_binary_payload()` を追加し、周期 256 のパターン (`bytes((index % 256) ...)`) を周期 65536 のパターン (`(index * 137 + index // 256) % 256`、32 KiB では反復しない) に置き換えた。周期 256 のパターンは 256 バイトの倍数のずれを原理的に検出できない (256・512・… が盲点)。適用先は 3 ファイル: `tests/test_e2e_http3.py` の `test_large_post_body` と損失注入テスト、`tests/test_e2e_webtransport_h3.py` の `test_large_stream_payload` (0144 が CI で失敗を観測したテスト)、`tests/test_quic_stream_retransmit.py` の `PAYLOAD` (0144 の回帰)
- assert の強化: `tests/test_e2e_http3.py` に `_assert_payload_matches(actual, expected, label)` を追加し、`test_large_post_body` の 2 箇所を置き換えた。不一致時にどちらの受信側か (label)・長さ・最初の不一致位置・期待値と実値の該当範囲を出力する。出力内容は `test_assert_payload_matches_reports_mismatch_details` で逐語的に検証する
- 損失注入テスト: `tests/test_e2e_http3.py` に `test_large_post_body_with_datagram_loss` を追加した。既存の `tests/lossy_relay.py` の `LossyRelay` (closed 0054 で新設) をクライアントとサーバーの間に挟み、方向別の通し番号で `drop_mod` 個に 1 個落とす決定的な規則を 3 通り (3・5・8) 回す。`relay.dropped` が双方向とも 0 でないことも表明し、注入が作用していない実行を検出する。待ち時間は CI と prek の pytest-timeout (30 秒) の内側に収めた
- RED と GREEN: `src/bindings/quic.cpp` の保持分を書き出し時に解放する 0144 修正前の挙動へ一時的に戻して rebuild すると、損失注入テスト 3 通りと `test_quic_stream_retransmit.py` 3 通りが失敗した。損失注入テストの失敗出力は「サーバーが受信したボディ が一致しません: 長さ expected=32768 actual=32768 最初の不一致位置=2254 expected[2246:2278]=… actual[2246:2278]=…」であり、どちらの受信側か・不一致位置・該当範囲が出力から分かる。修正を戻すと損失注入テスト 3 通り (ドロップ数は [3] が c2s 29 / s2c 27、[5] が 13 / 13、[8] が 7 / 7) と関連テストが全通過する
- 0144 との役割分担: 原因の特定と修正 (`QuicConnection::send()` の書き出し時の解放を受信確認ベースに変える) は closed 0144 が Sans-IO のロス注入で完了している。本 issue の作業は検出限界の解消と、0144 の修正が実ソケット E2E でも効いていることの確認に限定した。0034 の WRITE_MORE 契約は修正済みで、0144 は QUIC 層単体で再現することから H3 層は無関係と記録しており、同一経路の再探索は行っていない
- 検証: 損失注入テスト 3 通り、`test_large_post_body`、`test_assert_payload_matches_reports_mismatch_details`、`test_quic_stream_retransmit.py`、`test_e2e_webtransport_h3.py::test_large_stream_payload` が通過。全テスト 1342 passed
- 差分レビューの指摘 (docstring への issue 番号の記載、待ち時間が pytest-timeout の外側だった件、`parametrize` の ids 未指定、「非周期」という表現) を反映した
- 対象外: `LossyRelay` の機能追加、`src/` の変更 (0144 の修正で足りているかの確認が本 issue の目的)
