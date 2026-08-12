# HTTP/2 で WT_CLOSE_SESSION 受信後に send_datagram がデータグラムを送出する

- Created: 2026-08-11
- Completed: 2026-08-12
- Branch: feature/fix-h2-datagram-after-session-close
- Polished: 2026-08-12

## 目的

WebTransport over HTTP/2 の `send_datagram` が、WT_CLOSE_SESSION 受信後 (セッション終了を学習した後) にデータグラムカプセルをワイヤへ送出してしまう問題を修正する。HTTP/3 側で対応済み (issue 0057) と同種の問題が、HTTP/2 側の WT_CLOSE_SESSION 受信経路に残っている。

h2 仕様には h3 の Section 6 にある「新しいデータグラムを送信してはならない MUST (it MUST NOT send any new datagrams)」に相当する記述は存在しないが、draft-ietf-webtrans-http2-15 Section 6.12 の「WT_CLOSE_SESSION を受信したら END_STREAM で応答してストリームを閉じる MUST」と Section 3.4 の「セッション終了 = CONNECT ストリームのクローズ」により、受信後は終了を学習した状態とみなせる。本対応は仕様強制ではなく実装ポリシーである点に注意する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `send_datagram` は `send_capsule(session_id, CapsuleType::Datagram, data)` を呼ぶ
- `send_capsule` はセッションの存在確認 (`get_wt_session`) も確立状態の確認もせず、`http2_stream_buffers_` にカプセルを直接積む
- `handle_wt_close_session` は受信時に `is_established = false` にするが、HTTP/2 ストリームは送受信両ハーフが閉じるまで残る (ピアの END_STREAM のみでは half-closed (remote) のまま。確立済みセッションの `wt_sessions_` エントリ削除は `on_stream_close_callback` が両ハーフクローズ時に行う)
- Sans-IO 構成での実測・ソース分析の結果:
  - WT_CLOSE_SESSION 受信後に `send_datagram` を呼ぶと、データグラムカプセルが実際にワイヤへ送出される (ストリームが生存しているため)
  - 一度も `connect` されていないセッション ID 宛では nghttp2 のストリームが存在しないため送出されない (ただし `http2_stream_buffers_` にカプセルが残留する。`send_capsule` は `operator[]` でエントリを新規生成するため)
  - ローカル `close_session` (WT_CLOSE_SESSION 送出 + END_STREAM) 後は、END_STREAM の送出 (flush) 済みであれば送出されないが、flush 前に `send_datagram` を呼ぶとデータグラムカプセルが WT_CLOSE_SESSION の後ろに積まれて送出され得る (タイミング依存。`data_source_read_callback` はキューが空になってから EOF を返すため)
- セッション終了の定義は draft-ietf-webtrans-http2-15 Section 3.4 (CONNECT ストリームのクローズ)。WT_CLOSE_SESSION は終了前の通知であり、Section 6.12 の受信者側 MUST (END_STREAM で応答してストリームを閉じる) は現状未実装である (本 issue は送信側のガードで対処し、受信側の END_STREAM 応答はスコープ外)

## 設計方針

- `send_datagram` の冒頭で、セッションの存在確認 (`get_wt_session`) と終了状態の確認を行い、終了した・一度も `connect` されていないセッション ID への送信を黙って無視する。存在確認は終了フラグだけでは判定できない未 connect ID (エントリ不在) と、ストリームクローズ後にエントリが削除された ID を無視するために必要。終了状態の判定には `is_established` ではなく専用の終了フラグを使う: `is_established` は connect 直後 (200 応答前) も false のため、`is_established` で判定すると楽観的送信 (draft-ietf-webtrans-http2-15 Section 3.2 の MAY「クライアントは応答を待たずに WebTransport カプセル (データグラムはその例) を送信してよい」) がすべて無視されてしまう。終了フラグは WT_CLOSE_SESSION 受信時 (`handle_wt_close_session`) とローカル `close_session` 時に立てる (保持場所は WtSessionInfo への追加を想定)
- クライアントが非 2xx 応答 (拒否) を受けたセッション ID 宛の送信は本 issue のスコープ外とする (h3 側の issue 0061 が対象とした問題の h2 版。非 2xx 応答では終了フラグも `is_established` も立たず、エントリが残るため従来どおり送出される。別途の検討対象)
- チェックは `send_capsule` ではなく `send_datagram` に置く: `send_capsule` は `close_session` (WT_CLOSE_SESSION) / `drain_session` / `reset_stream` / `stop_sending` / フロー制御応答 (WT_MAX_DATA 等) の送信にも使われており、そこにチェックを入れると終了後の後始末カプセルまで塞がれる (0057 の h3 側の前例も `send_datagram` 単体)
- 楽観的送信は維持される: クライアントの connect 直後 (200 応答前) とサーバーの accept 前は終了フラグが立っていないため従来どおり送出される (サーバーも CONNECT リクエスト受信時に `wt_sessions_` へエントリを挿入する)
- ローカル `close_session` 後は終了フラグにより flush タイミングに依存せず送出されない (0057 の h3 側と同じ挙動に統一され、現状のタイミング依存が解消される)
- 終了を学習する前に `send_datagram` で `http2_stream_buffers_` に積まれたカプセルは、終了後に flush されるとそのまま送出され得る (0057 と同じ扱い。禁止対象は「新しいデータグラム」であり、既にキュー済みの送出はスコープ外)
- ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送る経路 (Section 3.4 の正規の終了経路) では `handle_wt_close_session` が呼ばれず終了フラグが立たないため、ストリームが閉じるまでの `send_datagram` は従来どおり送出される (本 issue のスコープ外)。同様に、確立前 (クライアントの 200 応答前・サーバーの accept 前) に受信した WT_CLOSE_SESSION は `on_data_chunk_recv_callback` の `is_established` 確認により処理されず終了フラグが立たない (本 issue のスコープ外。受信側の確立前カプセル処理は変更しない)
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (終了フラグと `send_datagram` の確認、`webtransport_h2.h` の `send_datagram` docstring に「終了したセッション ID への送信は無視される」旨を追記)、高レベル API の docstring (`src/webtransport/h2/client.py` / `src/webtransport/h2/server.py` の `send_datagram` にも同旨を追記。0057 の前例に合わせる)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ。HTTP/2 であることを文言で明記し、0057 のエントリと区別する)

## 完了条件

- WT_CLOSE_SESSION 受信後に `send_datagram` を呼んでもデータグラムがワイヤに送出されない
- ローカル `close_session` 後 (flush 前・後どちらでも) に `send_datagram` を呼んでも送出されない
- 生存セッションへの `send_datagram` は従来どおり送出される
- 一度も `connect` されていないセッション ID への送信は従来どおり送出されず、`http2_stream_buffers_` にも残留しない (回帰確認)
- クライアントの connect 直後 (200 応答前) とサーバーの accept 前の楽観的送信は従来どおり送出される
- モックなしの Sans-IO テストで検証できる (h2 には Sans-IO 双方向テスト基盤が存在しないため、conftest.py に h3 の `_create_session_pair` 相当の h2 用ヘルパー (preface / SETTINGS 交換 / CONNECT / accept / 200) を新設する)

## 解決方法

- `src/bindings/webtransport_h2.h` の `WtSessionInfo` に `is_terminated` フラグを追加し、`src/bindings/webtransport_h2.cpp` の `send_datagram` の冒頭でエントリ存在確認 (`get_wt_session`) と終了フラグの確認を行い、終了した・一度も connect されていないセッション ID への送信を黙って無視するようにした
- 終了フラグは WT_CLOSE_SESSION 受信時 (`handle_wt_close_session`) とローカル `close_session` 時に立てる。`close_session` では受信側と対称に `is_established` も false にし、`get_session_ids` からの消滅と `open_stream` の失敗 (セッション終了後の新規ストリーム開放の抑止) も行う
- チェックは `send_capsule` ではなく `send_datagram` に置いた (後始末カプセル WT_CLOSE_SESSION 等を塞がないため)
- 楽観的送信 (draft-15 Section 3.2 の MAY) は妨げない (`is_established` ではなく専用フラグで判定するため、connect 直後・accept 前も送出される)
- クライアントが非 2xx 応答を受けた ID 宛の送信と、ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送る終了経路は本 issue のスコープ外 (既知の制約) としてコメントに明記した
- `src/webtransport/h2/client.py` / `server.py` の `send_datagram` docstring に「終了したセッション ID への送信は無視される」旨を追記した
- `tests/conftest.py` に h2 用の Sans-IO 双方向ヘルパー (`_h2_pump` / `_create_h2_session_pair` / `_connect_h2_session`) を新設し、`tests/test_webtransport_h2_datagram.py` を新規追加した (9 件)。WT_CLOSE_SESSION 受信後・ローカル close_session 後 (flush 前・後) の送出抑止、close_session 後の open_stream 失敗と get_session_ids からの消滅、生存セッション・複数セッションでの送出継続、未 connect ID の無視、クライアント・サーバーの楽観的送信を検証する
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
