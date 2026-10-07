# h2 で上限超過カプセルを未知 Type でも WT_ERROR でセッション終了する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-oversized-unknown-capsule

## 目的

RFC 9297 Section 3.2 の「未知 Capsule Type は黙って破棄する MUST」と、本実装のメモリ DoS 対策 (単一カプセルのペイロード上限超過でセッションを閉じる) が衝突している箇所を解消する。上限超過の検査を、ペイロードをバッファする必要がある既知 Type に限定する。

## 現状

- RFC 9297 Section 3.2 は「Endpoints that receive a Capsule with an unknown Capsule Type MUST silently drop that Capsule and skip over it to parse the next Capsule.」と定める。Section 3.5 は大きな DATAGRAM capsule について「the implementation SHOULD discard the Capsule without buffering its contents into memory」とする
- `src/bindings/webtransport_h2.cpp` の `H2Session::process_capsules` は Length を解釈した直後、型ごとの分岐より前に `payload_len > config_.wt_max_capsule_payload_size` を検査し、超過時に `report_wt_error(session_id, "capsule payload exceeds limit")` でセッションを閉じる
- 上限内の未知 Type は switch に一致せず状態を変えずにスキップされ、MUST を満たしている。衝突するのは「未知 Type かつ上限超過」の場合だけである
- 0158 (closed) は上限超過時の WT_ERROR をメモリ DoS 対策として設計に記録しているが、未知 Type の MUST との衝突は記録していない
- 影響: 大きな未知カプセルや大きな DATAGRAM を送るピアに対して、仕様の期待 (無視 / 破棄) ではなくセッション断を返し、相互接続を壊す

## 設計方針

- 上限検査を「ペイロードをバッファする既知 Type」に限定する。未知 Type は Length だけ読んでペイロードをメモリへ確保せずに読み飛ばす。これにより DoS 対策 (無制限蓄積の防止) を維持したまま MUST を満たす
- DATAGRAM capsule は読み捨てる経路へ寄せる (Section 3.5 の SHOULD discard)
- ペイロードの読み飛ばしは HTTP/2 のフロー制御を消費するため、既存の部分読み (`wt_session->capsule_buffer` と未完成カプセルの待ち合わせ) と整合させる。読み飛ばし中に END_STREAM や RST_STREAM が来た場合の扱いも既存の切り詰め検証 (`verify_capsule_payload_fully_read` / `reset_stream_for_malformed_capsule`) と揃える
- 「上限超過の既知 Type」は従来どおり WT_ERROR でセッションを閉じる
- 変更対象: `src/bindings/webtransport_h2.cpp`、`tests/` (未知 Type の上限超過、巨大 DATAGRAM、既知 Type の上限超過の 3 系統)

## 完了条件

- 上限を超える未知 Type のカプセルを受信してもセッションが閉じず、後続のカプセルが処理される
- 上限を超える DATAGRAM を受信してもセッションが閉じず、そのカプセルが破棄され、後続のデータグラムが届く
- 上限を超える既知 Type のカプセル (WT_STREAM 等) は従来どおり WT_ERROR でセッションを閉じる
- 未知 Type / DATAGRAM の読み飛ばしでペイロードをバッファしないこと (メモリ DoS 対策の維持) を実装とテストで確認できる
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
