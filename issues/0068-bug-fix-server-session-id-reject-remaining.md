# サーバーが reject_session で拒否した後も session_ids_ からセッション ID が削除されない

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-server-session-id-reject-remaining
- Polished: 2026-08-12

## 目的

`reject_session` (403 等の非 2xx 応答) で CONNECT リクエストを拒否しても、サーバーの `session_ids_` にセッション ID が残り続け、拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出し、`receive_datagram` がデータグラムを配信し続ける問題を修正する。クライアント側の同種問題 (closed issue 0061: 非 2xx 応答受信時の残留) とは独立の経路である。

## 現状

- `src/bindings/webtransport_h3.cpp` の `reject_session` は `nghttp3_conn_submit_response` を呼ぶだけで、`session_ids_` からセッション ID を削除しない
- サーバー側の `session_ids_` への挿入は `end_headers_cb` (CONNECT リクエスト受信時) で行われるが、拒否されたセッションは削除経路 (`close_stream` / `close_session` / `recv_wt_close_session_cb`) のいずれにも該当せず、ID が残留する。0057 で実装済みの `send_datagram` のメンバーシップ確認は残留 ID を通過するため、拒否済みセッション ID 宛の `send_datagram` がデータグラムを送出し、`receive_datagram` も配信し続ける (受信側の配信継続は 0066 が「issue 0061 / 0068 実装後に閉じる依存関係としての既知の制約」と宣言済み)
- 実ネットワークでは、nghttp3 の実装挙動として 403 受信時に CONNECT ストリームを reset し、サーバー側は高レベル Server の STREAM_RESET 処理経由の `close_stream` で自浄される (このとき `close_stream` が残留 ID を CONNECT ストリームとして検出して SessionClosed を発火する)。Sans-IO 構成ではリセットが届かず残留が恒久化するため、問題は Sans-IO 構成で顕在化する
- 既存テスト `test_pre_accept_fin_not_accepted_keeps_session` (tests/test_webtransport_h3_pre_accept_fin.py) が「受理されない場合 (reject_session 経路) は SessionClosed が発火せず、セッション ID は残留する。現状の挙動を維持する」として残留をピン留めしている
- `reject_session` の呼び出し経路は 2 つある: (a) `end_headers_cb` 内の Origin 検証失敗による内部 403 (`session_ids_` への挿入前のため残留しない)、(b) アプリが SESSION_READY を受けて呼ぶ場合 (本 issue の対象)。高レベル Server (src/webtransport/h3/server.py) は SESSION_READY 受信時に自動で `accept_session` を呼ぶため、本問題が顕在化するのは低レベル API ユーザーである

## 設計方針

- `reject_session` で `session_ids_` からセッション ID を削除する (SessionClosed イベントは発火しない「黙って削除」)。根拠は次のとおり
  - draft-ietf-webtrans-http3-16 Section 3.2 は「From the server's perspective, a session is established once it sends a 2xx response」と定めており、非 2xx で拒否されたセッションはサーバー視点でも一度も確立されていない (0061 のクライアント側と同じ論理。SessionClosed は確立済みセッションの終了通知の意味論であり合わない)。サーバー側の SESSION_READY はライブラリの受理候補通知であり、仕様上の確立時点ではない
  - 拒否はアプリ自身の判断であり、終了通知を必要としない
  - 実ネットワークでは現状、拒否後のクライアント reset 到着時に `close_stream` 経路で SessionClosed が発火するが (Sans-IO 構成では観測されない)、本対応によりこの発火もなくなり「黙って削除」に統一される (挙動変化として許容する)
  - h2 サーバー側の `reject_session` も黙って `wt_sessions_.erase` しており (src/bindings/webtransport_h2.cpp)、「サーバーが拒否したら黙って削除」の前例がある (h2 側は既に自浄済みのため同種問題は存在しない)
- 削除条件は「`status_code` が 2xx 以外の場合」とする: `reject_session` は任意の status_code を受け付けており (既存テスト `test_client_response_201_session_kept_until_fin` が 201 応答の生成に使用。`accept_session` は 200 固定のため 2xx 非 200 応答は `reject_session` で生成する)、サーバー視点では 2xx 送出 = 確立 (Section 3.2) のため、2xx を渡した場合は何も削除しない (受理前 FIN 検知済みセッションに 2xx を渡した場合は `pending_pre_accept_fin_session_ids_` のエントリも残り、FIN によるセッション終了の意味論どおり送信がブロックされ続ける)
- `reject_session` で `pending_pre_accept_fin_session_ids_` のエントリも除去する (非 2xx の場合): 受理前 FIN 検知済みセッションの reject では、`session_ids_` の削除だけでは pending 集合にエントリが残留する (拒否されたセッションは `accept_session` による移行が発生しないため、除去が整合的)
- 拒否を学習する前に `send_datagram` で `pending_datagrams_` に積まれたデータグラムは、削除後に `get_datagrams_to_send` でそのまま送出される (0057 と同じ扱い。禁止対象は「新しいデータグラム」であり、既にキュー済みの送出はスコープ外)
- 削除後は `close_stream` の CONNECT ストリーム判定 (`session_ids_` のメンバーシップ確認) が成立しなくなり、以後の `close_stream` は 1 回目から -1 を返し SessionClosed も発火しない (0061 と同じ挙動変化。`close_stream` の docstring にサーバー側の拒否も -1 の対象である旨を追記する)
- 拒否後にピアが送ってくるデータストリーム (`recv_wt_data_cb`) とデータグラムは、`session_ids_` からの削除により既存のメンバーシップ確認で破棄される (受信側の後始末は本 issue のスコープ外。0061 と対称)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`reject_session` の修正と、`send_datagram` / `receive_datagram` / `close_stream` の docstring・実装コメントの更新。削除経路の列挙にサーバー側の拒否を追加し、「既知の制約」の記述を解消する)、`src/webtransport/h3/server.py` (受信側の「既知の制約」コメント 2 箇所の更新。0066 が宣言した受信側の制約が本実装で解消されるため)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `reject_session` (非 2xx) で拒否した後、サーバーの `session_ids_` からセッション ID が削除される (2xx を渡した場合は削除されない。サーバー側の `reject_session(0, 201)` 後に `get_session_ids()` が `[0]` のまま残ることも確認する)
- 拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出しない
- 拒否されたセッション ID 宛の `receive_datagram` がデータグラムを配信しない
- 拒否されたセッションに対して SessionClosed イベントが発火しない (黙って削除)
- 既存テスト `test_pre_accept_fin_not_accepted_keeps_session` を書き換え、「受理前 FIN 検知済みセッションの reject では `session_ids_` から削除されるが SessionClosed は発火しない」ことを確認する (受理前 FIN 検知時に即クローズしない = 未送信の 2xx を破棄しない、という本質の検証は既存の遅延クローズテスト `test_pre_accept_fin_deferred_close_waits_for_2xx` が担う)
- モックなしの Sans-IO テストで検証できる (conftest.py の `_create_session_pair` + `_setup_connect` で SESSION_READY 受信 → `reject_session(0, 403)` → `get_session_ids()` / `get_datagrams_to_send()` / `receive_datagram` の配信 / イベントを確認する構成)
