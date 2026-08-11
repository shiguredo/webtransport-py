# セッション終了後に終了したセッション ID 宛のデータグラムが配信される

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-datagram-delivery-after-session-close
- Polished: {YYYY-MM-DD}

## 目的

close_session (WT_CLOSE_SESSION 送出) と recv_wt_close_session_cb (WT_CLOSE_SESSION 受信) 後に、終了したセッション ID 宛のデータグラムが DATAGRAM イベントとしてアプリに配信され続ける問題を修正する。データストリーム経路 (issue 0059) は対応済みだが、データグラム受信経路が未対応のまま残っている。

## 現状

- `src/bindings/webtransport_h3.cpp` の `receive_datagram` はセッション ID の構造検証 (QUIC ストリーム ID の範囲チェック、closed issue 0049) のみを行い、`session_ids_` のメンバーシップを確認しない
- close_session 送出後・WT_CLOSE_SESSION 受信後に、そのセッション ID 宛のデータグラムを注入すると DATAGRAM イベントが発火する (Sans-IO 構成で実測確認済み)
- 0059 はデータストリーム経路のみを対象としており、データグラム受信経路はスコープ外とされた
- 根拠: draft-ietf-webtrans-http3-16 Section 4 の「closed session 宛のデータの扱いは Section 6 に従う (endpoints handle data for closed sessions as described in Section 6)」。終了したセッションのデータをアプリへ配信し続けるべきではない

## 設計方針

- `receive_datagram` でセッションの終了状態を確認し、終了したセッション ID 宛のデータグラムを破棄する (0059 の `recv_wt_data_cb` と同じ方針: `session_ids_` のメンバーシップ確認 + 受理前 FIN 検知済み集合の確認)
- 不正なセッション ID (QUIC ストリーム ID 範囲外) のデータグラムは、0049 の挙動を維持して H3_ID_ERROR で接続を閉じる (配信抑止と構造検証は独立)
- 破棄は Datagram イベントを発火しないことのみ (データグラムはトランスポート状態を持たないため、ストリームと異なり後始末は不要)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- close_session 送出後・WT_CLOSE_SESSION 受信後に、そのセッション ID 宛のデータグラムがアプリに配信されない
- 生存セッションのデータグラム受信は従来どおり配信される
- 不正なセッション ID (範囲外) のデータグラムは H3_ID_ERROR で接続が閉じられる (0049 の挙動維持)
- モックなしの Sans-IO テストで検証できる
