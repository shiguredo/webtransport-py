# WebTransport over HTTP/2 のサーバーが受理前に届いた楽観的カプセルを破棄する

- Created: 2026-09-06
- Completed: 2026-09-09
- Branch: feature/fix-h2-pre-accept-capsule-buffered
- Polished: 2026-09-07

## 目的

WebTransport over HTTP/2 のサーバーは `on_data_chunk_recv_callback` で `is_established` が false ならペイロードを捨てる。サーバー側の `accept_session` は Python 高レベル (`h2/server.py`) が `SESSION_READY` イベント取得後に呼ぶため、同一 TCP 読み取り内で HEADERS (CONNECT) と DATA (楽観的カプセル) が連続して届くと DATA は破棄される。draft-ietf-webtrans-http2-15 Section 3.2 はクライアントの楽観送信を MAY で許容し、受理前の処理禁止 (`MUST NOT process ... unless it accepts`) を定める。受理後の処理義務までは定めないが、h3 (draft-ietf-webtrans-http3-16 Section 3.2) の類推規定「受理前に届いたバイトは受理後に処理し、拒否時は破棄する」に倣い、H3 との対称性確保のための実装方針として受理後処理を行う。破棄のままでは楽観送信が機能しない相互運用性の問題である。h3 側は nghttp3 がバッファするため同じ問題は起きない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_data_chunk_recv_callback` は `wt_session && wt_session->is_established` を条件に `process_capsules` を呼ぶ (未確立なら黙って捨てる)
- サーバーの `accept_session` は Python 高レベル (`src/webtransport/h2/server.py` の `Server._handle_client`) が SESSION_READY イベント取得後に呼ぶ
- 実験 (Sans-IO。検証済み): H2Session ペアでクライアントが connect 直後に `send_datagram` し、サーバーが HEADERS と DATAGRAM カプセルを受信後に `accept_session` しても `Datagram` イベントは 0 件のままである (同一 `receive` でも別 `receive` でも破棄される方向性はコードのゲートから帰結する)
- `tests/test_webtransport_h2_datagram.py` の `test_send_datagram_client_optimistic_delivered` はクライアントがワイヤに出すことしか確認しておらず、サーバーが受理後に配信するかは検証していない

## 設計方針

- 受理前は既存 `capsule_buffer` (断片再構成と共用) に上限付きで蓄積する。受理前か否かにかかわらず到着順に追積みし、断片再構成と同一機構で扱う
- `accept_session` の C++ 処理内で 2xx 送出後に蓄積を `process_capsules` で処理し、`SessionReady` push 後に `Datagram` / `StreamData` イベントを発火させる。Python 高レベル層の変更は行わない (C++ イベント順序で配送される)。`accept_session` が失敗 (false) を返した場合は蓄積を破棄する
- 蓄積・処理の対象カプセル種別による振り分けは行わない (全種別を遅延処理する)。受理前 `WT_CLOSE_SESSION` も同様に遅延処理し、`SessionClosed` の二重発火がないことをテストで確認する
- `reject_session` で拒否した場合は蓄積を明示破棄する (非 2xx のエントリ削除に伴う自動消去に加え、2xx 拒否の `is_terminated` 経路でも破棄する)
- 蓄積の上限は新規 Config 項目として本 issue が定義する (既定 65536。`max_datagram_frame_size` 既定と整合する楽観的データグラム数発分の目安)。上限超過時は非 2xx (413) で拒否しバッファを破棄する (既存 `reject_session` 経路。接続は切らない)。0158 側の `capsule_buffer` 上限とは本 issue の上限定義を先行させ、0158 の deep 分離時に統合可否を判断する
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとし、Python 高レベル層の変更は行わない

## 完了条件

- クライアントの楽観的送信 (connect 直後の `send_datagram` / `send_stream_data`、同一 / 別 `receive` の両変種) が、サーバーの `accept_session` 後に `SessionReady` に続いて配信されること
- `reject_session` の場合は蓄積が破棄されること
- 蓄積の上限を超えた場合は非 2xx (413) で拒否されること
- `tests/test_webtransport_h2_datagram.py` に、受理前カプセルの配送テスト (同一 / 別 `receive`、`send_datagram` / `send_stream_data`、`SessionReady` 後の順序、上限超過の 413、`reject_session` 時の破棄、`WT_CLOSE_SESSION` の二重発火なし) を追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- サーバー側の受理前カプセルを既存 `capsule_buffer` に上限 (新規 Config `wt_pre_accept_buffer_limit`、既定 65536) 付きで蓄積し、`accept_session` の 2xx 送出後に遅延処理する。全種別を振り分けず処理する
- `reject_session` では蓄積を破棄し、上限超過時は非 2xx (413) で拒否して接続を保つ。`accept_session` 失敗時も破棄する
- 排出中のセッション削除に備えて `process_capsules` の取得を毎回取り直し、終了済みセッションに初期クレジットを送出しない
- `tests/test_webtransport_h2_datagram.py` に 9 件のテスト (同一/別 receive・stream・順序・413・破棄・二重発火なし・接続生存・境界値・不正混入) を追加する
- 全 915 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
