# WebTransport over HTTP/2 のサーバー開始ストリームのデータが WebKit で受信できない問題を修正する

- Created: 2026-08-07
- Completed: 2026-08-07
- Branch: feature/fix-h2-server-stream-webkit
- Polished: YYYY-MM-DD

## 目的

WebTransport over HTTP/2 でサーバーが開始したストリームのデータが、実ブラウザ (WebKit / Safari) で受信できない問題を修正する。実ブラウザとの相互運用性を確保するため。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::open_stream` は、ストリームを開くときに空の `WT_STREAM` capsule (ストリーム ID のみ、データなし) を送信し、後続の `send_stream_data` が `WT_STREAM_FIN` capsule でデータを送信する
- この「空の open capsule → データ付き FIN capsule」の順序は draft-ietf-webtrans-http2-15 の Section 6.4 に適合するが、WebKit の WebTransport over HTTP/2 実装は、空の capsule で開始されたストリームに対する `WT_STREAM_FIN` のデータを破棄する (ストリームは開くがデータが届かない)
- webtransport-py 自身のクライアント (Python) はこの順序でもデータを受信できるため、in-process のテストでは検出されない

## 設計方針

- `H2Session::open_stream` が送る空の `WT_STREAM` capsule を送信しないようにする。`WT_STREAM` capsule は最初のデータ送信 (`send_stream_data`) でストリームを暗黙的に作成する (draft-15 Section 6.4) ため、空 capsule を送らなくても仕様に適合する。WebKit はデータ付きの capsule でストリームが開始される場合は正しく受信できる
- API のシグネチャは変更しない

## 完了条件

- WebKit (Safari) の WebTransport API から、サーバーが開始した単方向ストリームのデータを受信できること (ブラウザ E2E テストで確認)
- 既存の in-process テスト (Python クライアント) が引き続き通ること

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::open_stream` が送る空の `WT_STREAM` capsule (ストリーム開始の通知) を送信しないようにした
- `WT_STREAM` capsule は最初のデータ送信 (`send_stream_data`) でストリームを暗黙的に作成する (draft-15 Section 6.4) ため、空 capsule を送らなくなっても仕様に適合する。WebKit はデータ付き capsule でストリームが開始される場合は正しく受信できる
- ブラウザ E2E テスト (WebKit の「サーバーからの単方向ストリーム送信」検証) と in-process テスト (Python クライアント) の両方で受信できることを確認した
