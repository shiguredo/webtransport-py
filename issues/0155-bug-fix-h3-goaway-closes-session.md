# h3 が GOAWAY 受信をプロトコルエラーとして切断する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-goaway-closes-session
- Polished: 2026-09-28

## 目的

`H3Session::shutdown_cb` は nghttp3 の shutdown コールバックで発火するが (GOAWAY 受信時に発火する)、内部で `closed_ = true` を立ててしまう。高レベル `h3.Client` / `h3.Server` は `is_closed()` を「プロトコルエラー」と解釈して `_close_on_protocol_error()` から `H3_GENERAL_PROTOCOL_ERROR` (0x0101) で QUIC の `CONNECTION_CLOSE` を送出する。draft-ietf-webtrans-http3-16 Section 4.7 は「GOAWAY 受信後もセッションを継続してよい」と定めており、RFC 9114 Section 5.2 は graceful shutdown 完了時に `H3_NO_ERROR` を使う SHOULD がある。正当な graceful shutdown をプロトコル違反として切断してしまう仕様違反。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::shutdown_cb` は `session->closed_ = true;` のみ
- `src/webtransport/h3/client.py` の `Client.run` は `if self._webtransport_session is not None and self._webtransport_session.is_closed(): ... await self._close_on_protocol_error(); ...` で `H3_GENERAL_PROTOCOL_ERROR` (`src/webtransport/http3/constants.py` の 0x0101) を送出
- `src/webtransport/h3/server.py` の `Server._handle_datagram` にも同型の分岐がある (クライアント側は `Client.run` の中にある)
- 対照: `src/bindings/http3.cpp` の `Http3Connection::shutdown_cb` は `GoAway` イベントを積むだけで `closed_` を立てない → 同じ nghttp3 コールバックの解釈が 2 ファイルで正反対
- `_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` で `shutdown` コールバックは GOAWAY 受信時に発火する (`conn->flags |= NGHTTP3_CONN_FLAG_GOAWAY_RECVED;` の直後)
- draft-16 Section 4.7 (refs 924-926 行) 「After sending or receiving either a WT_DRAIN_SESSION capsule or a HTTP/3 GOAWAY frame, an endpoint MAY continue using the session: it MAY open new WebTransport streams and MAY send new datagrams」
- RFC 9114 Section 5.2 「An endpoint that completes a graceful shutdown SHOULD use the H3_NO_ERROR error code when closing the connection」
- 同一クラス内での矛盾: `H3Session::unblock_stream` の doc「 GOAWAY 受信 (graceful shutdown) 後も既存ストリームのフロー制御ブロック操作は有効なため、closed_ は見ない」は GOAWAY 後の継続を前提にしている
- h3 系テスト (`tests/test_webtransport_h3*.py` / `tests/test_e2e_webtransport_h3.py`) に GOAWAY 受信を扱うテストは存在しない (当該パスでの `goaway` / `GOAWAY` / `shutdown_cb` の出現 0 件。素の `shutdown` 単語は QUIC 系と衝突するため対象外)

## 設計方針

- `H3Session::shutdown_cb` は `closed_` を立てない (現在の `noexcept` は維持する)。代わりにコネクション単位の新規イベント `H3EventType::GoAway` を積む。GOAWAY ID は `H3Event` に `uint64_t goaway_id` を追加して載せ、`bind_webtransport_h3` の `.def_ro("goaway_id", ...)` で公開する (`H2Event::last_stream_id` と対をなすが、HTTP/2 の GOAWAY は last stream ID、HTTP/3 の GOAWAY は GOAWAY ID のため名前は `goaway_id` とする)。セッション単位の `SessionDraining` 流用は選ばない (0198 が扱う `WT_DRAIN_SESSION` の受信通知とはイベントの区別があるため、0198 の所有物と衝突させない)
- 新規イベントは `H3EventType` の末尾に追加し、`h3.pyi` の `EventType` にも公開する (0133 / 0140 の確立規約に従い既存値の数値を変えない。CODEBASE の破壊許容は行使しない)。`bind_webtransport_h3` の enum 登録も追加する。`src/webtransport/webtransport_ext/h3.pyi` は nanobind の生成物であり手編集せず `make develop` で再生成し、追跡差分 (`EventType.GOAWAY` と `Event.goaway_id`) を含める
- 高レベル `Client` / `Server` は `GoAway` を `on_goaway` コールバックとして通知する。シグネチャは Client が `(goaway_id)`、Server が `(goaway_id, addr)` とし、既存の Client / Server 差 (後者が addr を取る) に合わせる。GOAWAY ID は方向で意味が異なる (RFC 9114 Section 7.2.6: クライアント→サーバー方向は push ID、サーバー→クライアント方向はクライアントが開始した要求の ID) ため、バインディングは ID による可否判定を行わず値の通知に留める。発火は初回受信のみとし、重複 GOAWAY の多重発火は抑止する。既存セッションの送受信は継続する
- 高レベル `Client.run` と `Server._handle_datagram` の `is_closed()` 分岐は変更しない。`shutdown_cb` が `closed_` を立てなくなるため GOAWAY 受信では `is_closed()` が偽のままになり、分岐は自動的に発火しない。分岐に GOAWAY 用の除外条件を足すと、GOAWAY 受信後の本物の接続エラー (nghttp3 の負値 return) の検出まで抑止して 0131 が直したハングが再発しうるため、変更するのは `is_closed()` の doc のみとする (「接続エラーを意味する nghttp3 の負値 return のときのみ真。GOAWAY 受信では真にならない」)
- 0131 由来の接続エラー注入テスト (`is_closed()` 真) との回帰両立を完了条件に含める (0131 は closed であり、`shutdown_cb` を変更しない前提は本 issue が覆す)
- 0170 (h2 版) で確定・実装済みの GoAway 方式 (enum 末尾追加・専用 ID フィールド・`on_goaway`・初回のみ通知) に h3 の単位・命名を合わせる。`drain_session` 送信側は対象外とする (所有 issue は無く、必要になった時点で起票する)

## 対象・対象外

- 対象: `src/bindings/webtransport_h3.cpp` (`shutdown_cb` と enum 登録、`.def_ro("goaway_id", ...)`) / `src/bindings/webtransport_h3.h` (`H3EventType::GoAway`、`H3Event::goaway_id`、`is_closed()` doc) / `src/webtransport/webtransport_ext/h3.pyi` (`make develop` による再生成。`EventType.GOAWAY` と `Event.goaway_id`) / `src/webtransport/h3/client.py` と `server.py` (`GoAway` イベント処理と `on_goaway` の追加。`is_closed()` 分岐は変更しない) / `tests/test_webtransport_h3_goaway.py` (新規、低レベル) と `tests/test_e2e_webtransport_h3.py` (高レベル)。`CHANGES.md` は変更しない (CODEBASE.md の指示)
- 対象外: GOAWAY 受信後の新規ストリームの open (nghttp3 が `NGHTTP3_CONN_FLAG_GOAWAY_RECVED` を立てて open 系を一律 `NGHTTP3_ERR_CONN_CLOSING` で拒否する実装であり、こちら側で緩和できない。フォークは CODEBASE.md で禁止。RFC 9114 Section 5.2 の "Endpoints MUST NOT initiate new requests" に適合するため仕様違反ではなく、draft-ietf-webtrans-http3-16 Section 4.7 の "MAY open new WebTransport streams" は許容規定) / `drain_session` 送信側 (所有 issue なし) / GOAWAY を送出する公開 API の新設 (h3 バインディングに送出手段が無く、本 issue は受信側の修正に限る。テストは低レベル `Session.receive_stream_data` への制御ストリーム注入で GOAWAY を発生させる) / `http3.cpp` / `http2.cpp` / `WT_DRAIN_SESSION` 受信経路 (0198 の所有)

## 完了条件

- GOAWAY 受信後に `is_closed()` が偽のままであること (低レベル Sans-IO で確認する。GOAWAY フレーム (`0x07` + Length + GOAWAY ID) を制御ストリームへ `Session.receive_stream_data` で注入して発生させる。h3 バインディングに GOAWAY 送出 API は無い)
- 継続を表明する試験の GOAWAY ID は、client 側セッションへ注入する場合は既存セッションの CONNECT ストリーム ID より大きい値 (例: 4 または 8) にする (RFC 9114 Section 5.2 は GOAWAY ID 以上の識別子の要求を拒否するため、ID 0 では「既存セッションを継続できる」ことを表明できない)。server 側セッションへ注入する GOAWAY の ID は push ID を表す (RFC 9114 Section 7.2.6) ため、この数値要件は client 側の継続表明に対するものである
- GOAWAY 受信後も、確立済みセッションの既存ストリームと datagram の送受信を継続できること (低レベル Sans-IO で確認する)
- 高レベル層が `H3_GENERAL_PROTOCOL_ERROR` を送出しないこと
- 初回 GOAWAY 受信で `on_goaway` が 1 回発火すること (クライアント側は `(goaway_id)`、サーバー側は `(goaway_id, addr)`。重複 GOAWAY で多重発火しないこと)
- 0131 由来の接続エラー注入テストが引き続き `is_closed()` 真を維持すること (回帰両立。`is_closed()` 分岐を変更していないことの確認でもある)
- `tests/test_webtransport_h3_goaway.py` (新規) に GOAWAY フレーム注入による継続テスト (制御ストリームへ注入し、`is_closed()` 偽 + `GoAway` イベント + 既存ストリーム / datagram の送受信継続を表明) を追加し、`tests/test_e2e_webtransport_h3.py` に `H3_GENERAL_PROTOCOL_ERROR` 不送出と `on_goaway` 発火のテストを追加すること (e2e は `Client._webtransport_session.receive_stream_data` への制御ストリーム注入で GOAWAY を発生させる。e2e のピアは `webtransport.h3.Server` であり、`Server._setup_streams` → `open_http3_uni_streams` が制御 → QPACK エンコーダ → QPACK デコーダの順に開くため、サーバー起動の単方向ストリームは制御 3 / エンコーダ 7 / デコーダ 11 に確定する。ピアの制御ストリーム ID を外から観測する API は無いため、確定した 3 を使う)
- 全テストが通過すること

## reopened にした理由

- pending にした理由に記録した再開条件のうち 2 つ目 (「完了条件を『既存継続と通知のみ』に絞り直し、新規 open を対象外として再開する」) を選び、完了条件を絞り直したうえで再開する。1 つ目の「nghttp3 上流が GOAWAY ID 考慮の open 可否に対応する」は未達であり、新規 open は引き続き対象外とする
- 本来の不具合は現行の develop でも未修正であることを確認した。`src/bindings/webtransport_h3.cpp` の `H3Session::shutdown_cb` は `session->closed_ = true;` のままで、`src/webtransport/h3/client.py` の `Client.run` と `src/webtransport/h3/server.py` の `Server._handle_datagram` は `is_closed()` をプロトコルエラーとして扱い `H3_GENERAL_PROTOCOL_ERROR` の `CONNECTION_CLOSE` を送出する分岐を持っている。対照の `src/bindings/http3.cpp` の `Http3Connection::shutdown_cb` はイベントを積むだけで `closed_` を立てないままであり、同じ nghttp3 コールバックの解釈が 2 ファイルで正反対の状態が続いている
- pending 中に develop が進み、`src/bindings/webtransport_h3.cpp` / `.h` / `src/webtransport/h3/*.py` / テストの構成が変わった。作業ブランチ `feature/fix-h3-goaway-closes-session` (コミット `1e53979`) の部分修正は流用候補として残っているが、develop との差が大きいため、そのままマージせず現行の構成に合わせて実装し直す
- 再開にあたり issue の記述を現行の構成と規約に合わせて更新した: 型スタブのパスを `src/webtransport/webtransport_ext/h3.pyi` に修正し、`CHANGES.md` を変更対象から外し (CODEBASE.md の指示)、完了条件の「新規ストリームの open」を対象外へ移した

## pending にした理由

- 完了条件の「新規ストリームの open が可能」が達成できないため保留する。残りの条件は作業ブランチで満たせる見込みであり、切り分けを以下に記録する
- 動作する範囲: GOAWAY 受信後に `is_closed()` が偽のままになること、`GoAway` イベントと `on_goaway` の初回のみ発火、確立済みストリームと datagram の送受信継続、`H3_GENERAL_PROTOCOL_ERROR` の不送出、接続エラー時の `is_closed()` 真の維持は、いずれも作業ブランチで確認済みである
- nghttp3 側の制約: 同梱 nghttp3 は GOAWAY 受信後に `NGHTTP3_CONN_FLAG_GOAWAY_RECVED` を立て、新規 open 系 (`nghttp3_conn_open_wt_data_stream` と request 送出系) を一律 `NGHTTP3_ERR_CONN_CLOSING` で拒否する。ソース内の `TODO Check GOAWAY last stream ID` が示すとおり GOAWAY ID による可否判定は未実装であり、ID の値によらない一律拒否である。フォークは規約で禁止のため、こちら側で緩和できない
- nghttp3 の一律拒否は仕様違反ではない。`refs/h3/rfc9114.txt` Section 5.2 は「Endpoints MUST NOT initiate new requests」と定めており、新規 open を拒否する実装はこの MUST に適合する。draft-ietf-webtrans-http3-16 Section 4.7 の「MAY open new WebTransport streams」は許容規定であり、拒否しても違反にならない
- issue 側の判断ミス: 完了条件は上記 MAY を必須と読み違えていた。加えて GOAWAY ID の意味論の考慮が漏れていた。`refs/h3/rfc9114.txt` Section 5.2 は GOAWAY ID 以上の新規要求を拒否することを求めており、ID によっては新規 open が正当に拒否される。テストの注入値と表明の組み合わせでは ID 制約の分離ができていなかった
- よって原因は両方である。nghttp3 の保守的な実装と、issue の完了条件の書き過ぎが重なっている。`shutdown_cb` が `closed_` を立てる本来の不具合 (protocol-error close) とは別の層の話であり、切り分けて記録する
- 再開条件: nghttp3 上流が GOAWAY ID 考慮の open 可否に対応したら reopened にする。または完了条件を「既存継続と通知のみ」に絞り直し、新規 open を対象外として再開する。作業ブランチは残し、部分修正は再開時に流用する
