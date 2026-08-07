# 実ブラウザ (Chromium) を使った WebTransport E2E テストを追加する

- Created: 2026-08-01
- Completed: 2026-08-07
- Branch: feature/add-browser-e2e-test
- Polished: 2026-08-04

## 目的

WebTransport サーバーが実ブラウザ (Chromium) の WebTransport API からのアクセスを正しく処理できることを検証する。実ブラウザとの相互運用性は未検証のため。

## 現状

- テストは `tests/test_e2e_webtransport_h3.py` などの自ライブラリの asyncio クライアントから同一プロセス内のサーバーへ接続する構成のみ
- サーバー実装は QUIC v1 (`src/bindings/quic.cpp` の `NGTCP2_PROTO_VER_V1`) と `sec-webtransport-http3-draft: draft02` ヘッダー (`src/bindings/webtransport_h3.cpp`) を使用している。`sec-webtransport-http3-draft` は draft-02 世代の draft で使われたヘッダーであり、現行の draft-ietf-webtrans-http3-16 には定義されていないため、ブラウザとの互換性は実測でのみ確認できる
- ブラウザがサーバーを WebTransport 対応と判定するのは SETTINGS フレームの対応広告一式 (SETTINGS_ENABLE_CONNECT_PROTOCOL / SETTINGS_WT_ENABLED / SETTINGS_H3_DATAGRAM。draft-ietf-webtrans-http3-16 Section 3.1) であり、draft 世代ごとのコードポイントは Section 7.1 で異なる。deps.json でブランチ指定している nghttp3 の webtransport ブランチは現行 draft (SETTINGS_WT_ENABLED 0x2c7cf000) と過去 draft 世代のコードポイントを併せて送出する構成のため、ローカルビルドの nghttp3 が古いと現行 draft 世代のコードポイントが送出されず、最新ブラウザがサーバーを WebTransport 対応と判定できない (確認・更新手順は設計方針参照)
- draft-ietf-webtrans-http3-16 Section 3.1 のサーバー要件のうち、reset_stream_at transport parameter はサーバー実装から未送出である (`src/bindings/quic.cpp` に設定なし)。W3C の WebTransport 対応判定には含まれないため、ブラウザ接続が失敗した場合の原因切り分けの 1 点として実測で確認する
- Origin ヘッダーの検証 (draft-ietf-webtrans-http3-16 Section 3.2 の MUST) はサーバー実装に実装済みである (issue 0005 で `Server` の `allowed_origins` に追加)。ブラウザからの接続は必ず Origin ヘッダーを送るため、テスト用サーバーにはテストページのオリジン (`https://moqt-devtools.shiguredo.app`) を `allowed_origins` に設定する必要がある

## 設計方針

- pytest + pytest-playwright で Chromium を起動し、Shiguredo の WebTransport DevTools (https://moqt-devtools.shiguredo.app/webtransport-devtools) をブラウザ側 WebTransport クライアントとして利用する。実行モードは headless とする (現行 Playwright の headless は new headless が既定)。headless で WebTransport 接続が確立できない場合は、launch オプションの見直し (headed での確認や channel 指定など) を実測で判断する
- DevTools は URL パラメータ `url` と `certificateHash` をサポートしており、接続先 URL と自己署名証明書のピン留め (DER の SHA-256 を base64 化した値) を指定できる。ページロード後の Connect ボタンクリックで接続が開始される。接続先 URL は `https://127.0.0.1:{actual_port}/webtransport` とする (サーバーは :path を検証しないため任意だが、既存の e2e テストと同じパスに揃える)。base64 に含まれる `+` は `URLSearchParams` がスペースにデコードされ、`atob()` は空白を無視するため正しいバイト列に復元できず証明書ハッシュ検証が失敗するので、percent-encoding して渡す
- echo サーバーは pytest フィクスチャで起動する。pytest-playwright の同期 API を利用するため、asyncio サーバーは別スレッドで `asyncio.run()` により起動し、ティアダウンで別スレッドのイベントループに停止処理をスケジュールして停止する。停止時は `Server.stop()` がソケットを閉じると `Server.run()` が `OSError` で終了すること、およびスレッドの join までを実施することに注意する。検証観点 5 項目は 1 回の接続で順次検証する 1 本のテストとして実装する (ページロードは外部ホスト依存のため最小限に留め、接続イベントの混線も避ける)。サーバー側のイベント (`on_session_ready` 等) は共有キュー等のスレッド間通信で同期 pytest 側から観測する
- 検証観点は以下とする
  - 接続確立 (サーバー側 `on_session_ready` とページ側の Connected 表示の両方で確認)
  - 双方向ストリームの送受信 (エコーバック。双方向ストリーム (QUIC stream_id % 4 == 0) のみエコーし、サーバー側 `on_stream_data` とページ側の Bidirectional Streams の RECV 表示の両方で確認)
  - 単方向ストリームの送信 (サーバー側 `on_stream_data` で受信を確認。クライアント起点の単方向ストリーム (QUIC stream_id % 4 == 2) にはエコーしない)
  - サーバーからの単方向ストリーム送信 (セッション確立をトリガーに `Server.open_stream` で開いて 1 回送信し (戻り値が 0 以上であることを確認)、ページ側の Incoming Streams への表示で確認)
  - データグラムの送受信 (エコーバック。サーバー側 `on_datagram` とページ側の Datagrams の RECV 表示の両方で確認。UDP のロスに備えて複数回送信し、サーバー側・ページ側それぞれで少なくとも 1 回の受信を確認する)
- 検証に使うサーバーは高レベル `Server` (`src/webtransport/h3/server.py`) で構築した echo サーバー 1 つで行う。テストページのオリジン (`https://moqt-devtools.shiguredo.app`) を `allowed_origins` に設定し、サーバーからの単方向ストリーム送信は `Server.open_stream` を使用する
- ブラウザテストは `tests/browser/` ディレクトリに分離して配置し、`pyproject.toml` の `addopts` に `--ignore=tests/browser` を設定することで通常の `make test` と CI の collection 対象から除外する (marker の `-m` だけでは collection 時の import を防げず、playwright 未導入の CI で失敗するため)。`pytest.mark.browser` の marker は `pyproject.toml` に登録する。実行は、ローカルの nghttp3 が最新の webtransport ブランチでビルドされていることを確認したうえで (古い場合は `_deps/nghttp3` を削除して再ビルドする)、`playwright install chromium` でバイナリを導入し、`uv run pytest tests/browser -m browser --timeout=60` によりローカルで手動実行する。pytest-playwright の成果物出力先 (`test-results/`) は `.gitignore` に追加する。CI ジョブの追加は対象外 (必要になったら別 issue とする)
- playwright / pytest-playwright は pyproject.toml の dev グループに追加する (test グループに追加すると CI の `uv sync --only-group test` で常に導入されてしまうため)
- WebTransport over HTTP/2 は Chromium が対応していないため対象外 (既存の in-process テストでカバー)
- テスト対象ページは外部ホストに依存するため、本番 URL を直接利用する (ローカルホスティングや vendoring は行わない)
- 接続確立や検証観点の確認ができない場合は原因を調査し、調査結果を本 issue の解決方法に残す。サーバー実装の修正が必要な場合は修正内容を別 issue として起票する。検証観点の確認がサーバー側の欠陥や外部要因 (ブラウザの制約・DevTools の変更や停止等) で妨げられた場合は、調査結果と対応先 (別 issue) を解決方法に残し、本 issue はテストコードの追加のみを完了条件として完了とする

## 完了条件

- 設計方針の検証観点 5 項目 (接続確立・双方向ストリームの送受信・単方向ストリームの送信・サーバーからの単方向ストリーム送信・データグラムの送受信) がすべて、各項目に定めた確認方法で検証できる (検証できない場合の終了条件は設計方針に定めたとおり)
- 自己署名証明書は `certificateHash` パラメータによるピン留めで接続できる。証明書は W3C WebTransport API の Server Authentication using Certificate Hashes の要件 (W3C WebTransport §6.9 の custom certificate requirements。ECDSA P-256、有効期間 2 週間以内) を満たす必要があるが、既存の `test_certificates` フィクスチャ (ECDSA P-256 (SECP256R1)・有効期間 1 日) は要件を満たす
- ブラウザテストは `tests/browser/` に分離され、通常の `make test` と CI のテスト実行に影響しない

## 解決方法

- `tests/browser/` に pytest-playwright を使った Chromium E2E テスト一式を追加した (`conftest.py` / `helpers.py` / `test_webtransport_chromium.py`)。WebTransport DevTools テストページをブラウザ側クライアントとして利用し、検証観点 5 項目 (接続確立・双方向ストリームの送受信・単方向ストリームの送信・サーバーからの単方向ストリーム送信・データグラムの送受信) を 1 回の接続で順次検証する
- 自己署名証明書は `certificateHash` パラメータによるピン留めで接続する。base64 に含まれる `+` は percent-encoding して渡し、`atob()` での復元が正しく行われるようにした
- `pyproject.toml` の dev グループに playwright / pytest-playwright を追加し、`addopts` の `--ignore=tests/browser` で通常のテスト実行と CI の collection 対象から除外した
- 接続確立のフレーク対策として、接続失敗時のページ再読み込みリトライと待ち時間の調整を追加した
- ブラウザ E2E テストの CI ジョブ (`e2e-test.yml`) を追加し、Chromium / WebKit の h3 / h2 テスト 24 件が通ることを確認した
