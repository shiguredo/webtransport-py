# 実ブラウザ (Chromium) を使った WebTransport E2E テストを追加する

- Created: 2026-08-01
- Completed: YYYY-MM-DD
- Branch: feature/add-browser-e2e-test
- Polished: 2026-08-01

## 目的

WebTransport サーバーが実ブラウザ (Chromium) の WebTransport API からのアクセスを正しく処理できることを検証する。現在のテストは自ライブラリのクライアントとサーバーの組み合わせだけであり、実ブラウザとの相互運用性は未検証のため。

## 現状

- テストは `tests/test_e2e_webtransport_h3.py` などの自ライブラリの asyncio クライアントから同一プロセス内のサーバーへ接続する構成のみ
- サーバー実装は QUIC v1 (`src/bindings/quic.cpp` の `NGTCP2_PROTO_VER_V1`) と `sec-webtransport-http3-draft: draft02` ヘッダー (`src/bindings/webtransport_h3.cpp`) を使用しているが、実際にブラウザから接続して確認できていない。`sec-webtransport-http3-draft` は draft-ietf-webtrans-http3-02 世代のヘッダーであり、現行の draft-ietf-webtrans-http3-16 には定義されていない。ブラウザとの互換性は実測でのみ確認できる
- Origin ヘッダーの検証 (draft-ietf-webtrans-http3-16 Section 3.2 の MUST) はサーバー実装に存在しない。ブラウザからの接続は必ず Origin ヘッダーを送るが、本 issue では相互運用性の検証のみを対象とし、Origin 検証の実装は対象外とする

## 設計方針

- pytest + playwright-pytest で Chromium を起動し、Shiguredo の WebTransport DevTools (https://moqt-devtools.shiguredo.app/webtransport-devtools) をブラウザ側 WebTransport クライアントとして利用する。実行モードは headless (画面なしで実ブラウザエンジンを実行) とする。headless で WebTransport 接続が確立できない場合は playwright の launch オプション (headless="new" 等) を検討する
- DevTools は URL パラメータ `url` と `certificateHash` をサポートしており、接続先 URL と自己署名証明書のピン留め (DER の SHA-256 を base64 化した値) を指定できる。ページロード後の Connect ボタンクリックで接続が開始される。base64 に含まれる `+` は `URLSearchParams` がスペースにデコードして `atob()` が失敗するため、percent-encoding して渡す
- echo サーバーは pytest フィクスチャで起動する。playwright-pytest は同期 API のため、asyncio サーバーは別スレッドで `asyncio.run()` により起動し、ティアダウンで別スレッドのイベントループに停止処理をスケジュールして停止する。サーバー側のイベント (`on_session_ready` 等) は共有キュー等のスレッド間通信で同期 pytest 側から観測する
- 検証観点は以下とする
  - 接続確立 (サーバー側 `on_session_ready` とページ側の Connected 表示の両方で確認)
  - 双方向ストリームの送受信 (エコーバック。サーバー側 `on_stream_data` とページ側の RECV 表示の両方で確認)
  - 単方向ストリームの送信 (サーバー側 `on_stream_data` で受信を確認。クライアント起点の単方向ストリームにはエコーしない)
  - サーバーからクライアントへの一方的なストリーム送信 (ページ側の Incoming Streams への表示で確認)
  - データグラムの送受信 (エコーバック。サーバー側 `on_datagram` とページ側の RECV 表示の両方で確認。UDP のロスに備えて複数回送信し、少なくとも 1 回の受信を確認する)
- 検証に使うサーバーは 2 つに分ける。接続確立・双方向ストリーム・単方向ストリーム送信・データグラムは高レベル `Server` (`src/webtransport/h3/server.py`) で構築した echo サーバーで行う。サーバーからクライアントへの単方向ストリーム送信は、高レベル `Server` にストリームを開く API が無いため、低レベル API (`webtransport.h3.Session` と `quic.Connection`) で構築したテスト専用のサーバーで行う
- ブラウザテストは `tests/browser/` ディレクトリに分離して配置し、`pyproject.toml` の `addopts` に `--ignore=tests/browser` を設定することで通常の `make test` と CI の collection 対象から除外する (marker の `-m` だけでは collection 時の import を防げず、playwright 未導入の CI で失敗するため)。`pytest.mark.browser` の marker は `pyproject.toml` に登録する。実行は `playwright install chromium` でバイナリを導入したうえで `uv run pytest tests/browser -m browser --timeout=60` によりローカルで手動実行する。CI ジョブの追加は対象外 (必要になったら別 issue とする)
- playwright / pytest-playwright は pyproject.toml の dev グループに追加する (test グループに追加すると CI の `uv sync --only-group test` で常に導入されてしまうため)
- WebTransport over HTTP/2 は Chromium が対応していないため対象外 (既存の in-process テストでカバー)
- テスト対象ページは外部ホストに依存するため、本番 URL を直接利用する (ローカルホスティングや vendoring は行わない)

## 完了条件

- 設計方針の検証観点 5 項目 (接続確立・双方向ストリーム・単方向ストリーム送信・サーバーからの単方向ストリーム・データグラム) がすべて、各項目に定めた確認方法で検証できる
- 自己署名証明書は `certificateHash` パラメータによるピン留めで接続できる。証明書は W3C WebTransport API の要件 (Server Authentication using Certificate Hashes。ECDSA P-256、有効期間 2 週間以内) を満たす必要があるが、既存の `test_certificates` フィクスチャは要件を満たす
- ブラウザテストは `tests/browser/` に分離され、通常の `make test` と CI のテスト実行に影響しない
