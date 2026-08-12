# セッション終了後に終了したセッション ID 宛のデータグラムが配信される

- Created: 2026-08-11
- Completed: 2026-08-12
- Branch: feature/fix-datagram-delivery-after-session-close
- Polished: 2026-08-12

## 目的

close_session (WT_CLOSE_SESSION 送出) と recv_wt_close_session_cb (WT_CLOSE_SESSION 受信) の後、および CONNECT ストリームのクローズ (close_stream) 後に、終了したセッション ID 宛のデータグラムが DATAGRAM イベントとしてアプリに配信され続ける問題を修正する。データストリーム受信経路 (issue 0059) は対応済みだが、データグラム受信経路が未対応のまま残っている。

受信データグラムの破棄は仕様の MUST 由来ではなく実装ポリシーである点に注意する: draft-ietf-webtrans-http3-16 Section 6 の MUST (終了を学習したエンドポイントは属するストリームの受信側の読み取りを中止し、新しいデータグラムを送信しない) はストリームと送信側を対象としており、受信データグラムの破棄を直接要求しない。根拠は Section 4 の「closed session 宛のデータの扱いは Section 6 に従う (endpoints handle data for closed sessions as described in Section 6)」と、データグラムは再送されず配信保証がないこと (Section 4.1)、破棄が許容される機構であること (Section 4.6) に置く。

## 現状

- `src/bindings/webtransport_h3.cpp` の `receive_datagram` はセッション ID の構造検証 (QUIC ストリーム ID の範囲チェック、closed issue 0049) のみを行い、`session_ids_` のメンバーシップを確認しない
- close_session 送出後・WT_CLOSE_SESSION 受信後に、そのセッション ID 宛のデータグラムを注入すると DATAGRAM イベントが発火する (Sans-IO 構成で実測確認済み)。CONNECT ストリームのクローズ経路 (close_stream) でも同様に配信され続ける (データグラム受信経路には、0059 のデータストリーム受信と違い「既に正しく動作する経路」が存在しない)
- 0059 はデータストリーム受信経路のみを対象としており、データグラム受信経路には対応していない
- closed issue 0049 の完了条件「正常なセッション ID のデータグラムは従来どおり配送される (閉じたセッションの ID も含む)」と、それをピン留めする e2e テスト `test_datagram_closed_session_id_still_delivered` (tests/test_e2e_webtransport_h3.py) は本 issue で覆す。0057 が「受信側の検証 (0049 の意図) を維持するため」QUIC 層への直接注入に書き換えて維持した経緯も同様に覆す。0049 の「Session IDs that correspond to closed sessions are not considered invalid for the purposes of this check」は構造検証 (H3_ID_ERROR) の対象外という意味であり、配信の要否には言及しない

## 設計方針

- `receive_datagram` で、構造検証 (0049 の範囲チェック) の**後**にセッションの終了状態を確認し、終了したセッション ID 宛のデータグラムを破棄する (0059 の `recv_wt_data_cb` と同じ方針: `session_ids_` のメンバーシップ確認 + 受理前 FIN 検知済み集合 (`pending_pre_accept_fin_session_ids_` / `pre_accept_fin_accepted_session_ids_`) の確認)。受理前 FIN 検知済みセッションの確認は、データグラム経路では nghttp3 のバッファリングが無く 2xx 書き出し完了まで `session_ids_` に残るため、機能として必須である (0059 での「防御的」という位置づけとは異なる)
- 順序は「範囲チェック → 終了状態確認」とする: 範囲外 ID は先に H3_ID_ERROR で接続を閉じる (0049 の挙動維持)。終了状態確認を先に置くと範囲外 ID が黙って破棄される
- 一度も確立されていないセッション ID 宛のデータグラムも破棄される (低レベル API の意味論の変更。0057 の送信側と同じ扱い)。サーバー側では CONNECT ヘッダー処理前 (`end_headers_cb` による `session_ids_` 挿入前。QPACK デコードブロック中を含む) に届く楽観的データグラムが破棄されるが、これは draft Section 4.6 の SHOULD バッファリングに対する許容された逸脱とする (データグラムは再送されず配信保証がないため喪失は無害。既存実装もバッファリングを行っていない)
- クライアント側の connect 直後 (200 応答前) とサーバー側の受理前 (accept_session 前) のデータグラム受信は `session_ids_` に含まれるため従来どおり配信される (楽観的送受信は妨げない)
- 不正なセッション ID (範囲外) のデータグラムは 0049 の挙動を維持して H3_ID_ERROR で接続を閉じる (配信抑止と構造検証は独立)
- 破棄は Datagram イベントを発火しないことのみ (データグラムはトランスポート状態を持たないため、ストリームと異なり後始末は不要)
- 非 2xx 応答 (拒否) を受けたセッション ID 宛のデータグラムは、issue 0061 / 0068 が未実装の間は `session_ids_` に ID が残るため配信され続ける (0061 / 0068 実装後に閉じる依存関係としての既知の制約)
- WebTransport over HTTP/2 の受信側データグラム配信 (本 issue の h2 相当) は本 issue のスコープ外とする
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`receive_datagram` の終了状態確認と既存コメント (「検証は構造のみで行い、閉じたセッションの ID は検査対象外」) の更新、`webtransport_h3.h` の `receive_datagram` docstring への意味論変更の反映)、高レベル API の docstring と DATAGRAM 分岐コメント (`src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` の「閉じたセッションの ID も含む」記述と `on_datagram` の docstring の更新)、テスト (既存 e2e テスト `test_datagram_closed_session_id_still_delivered` の書き換え (テスト名の変更を含む。期待値の反転と、構造検証が維持されて接続が閉じないことの検証) と新規 Sans-IO テスト (3 経路すべてを検証する。close_stream 経路も新規 Sans-IO テストで担保する))、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- close_session 送出後・WT_CLOSE_SESSION 受信後・CONNECT ストリームのクローズ後のいずれでも、そのセッション ID 宛のデータグラムがアプリに配信されない
- 受理前 FIN 検知済みセッション宛のデータグラムも配信されない
- 一度も確立されていないセッション ID 宛のデータグラムも配信されない (低レベル API の意味論の変更)
- 生存セッションのデータグラム受信は従来どおり配信される
- 不正なセッション ID (範囲外) のデータグラムは H3_ID_ERROR で接続が閉じられる (0049 の挙動維持)
- モックなしの Sans-IO テストで検証できる (conftest.py の `_encode_wt_datagram` と `_establish_session` を流用し、セッション終了後に `receive_datagram` へ直接注入する構成)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `receive_datagram` で、構造検証 (範囲チェック。H3_ID_ERROR) の**後**にセッションの終了状態を確認し、終了した・一度も確立されていないセッション ID 宛のデータグラムを破棄して Datagram イベントを発火しないようにした。確認条件は受信データストリームの破棄 (`recv_wt_data_cb`) と同じ方針: `session_ids_` のメンバーシップ + 受理前 FIN 検知済み集合 (`pending_pre_accept_fin_session_ids_` / `pre_accept_fin_accepted_session_ids_`) の確認
- 受理前 FIN 検知済みセッションの確認は、データグラム経路では nghttp3 のバッファリングが無く 2xx 書き出し完了まで `session_ids_` に残るため必須 (破棄されないと終了を学習した後に配信され続ける)
- 受信データグラムの破棄は仕様の MUST 由来ではなく実装ポリシーであることをコメントに明記した (根拠: draft-ietf-webtrans-http3-16 Section 4 の「closed session 宛のデータの扱いは Section 6 に従う」、Section 4.1 / RFC 9221 の「データグラムは再送されず配信保証がない」)
- サーバー側は CONNECT リクエストの処理完了前 (QPACK デコードブロック中を含む) に届いたデータグラムが破棄される (楽観的データグラムの先行到着で喪失し得る。draft Section 4.6 の SHOULD バッファリングに対する許容された逸脱) 旨と、サーバー側の `reject_session` (非 2xx 拒否) は `session_ids_` から削除しないため拒否されたセッション ID 宛のデータグラムは配信され続ける (既知の制約) 旨をコメントに明記した
- `src/bindings/webtransport_h3.h` の `receive_datagram` docstring と、`src/webtransport/h3/client.py` / `server.py` の DATAGRAM 分岐コメント・`on_datagram` docstring を意味論の変更に合わせて更新した
- `tests/test_webtransport_h3_datagram.py` に受信テスト 10 件を追加した: close_session 送出後・WT_CLOSE_SESSION 受信後・CONNECT ストリームクローズ後の破棄 (3 経路)、受理前 FIN 検知済み (pending / accepted の 2 状態) の破棄、未確立 ID の破棄、生存セッションの配信、クライアント connect 直後・サーバー受理前の楽観的受信の配信、範囲外 ID の H3_ID_ERROR
- `tests/test_e2e_webtransport_h3.py` の `test_datagram_closed_session_id_still_delivered` を `test_datagram_closed_session_id_discarded` に書き換えた (期待値の反転と、構造検証が維持されて接続が閉じないこと・生存セッションへの配信継続の検証)
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
