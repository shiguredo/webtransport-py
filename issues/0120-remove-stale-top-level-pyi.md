# 古いスナップショットのまま残るトップレベル .pyi を削除する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-stale-top-level-pyi
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/` 直下に残る quic.pyi / http2.pyi / http3.pyi が、パッケージ版の型スタブ (quic/__init__.pyi 等) に置き換わった古いスナップショットのまま死にファイル化している。削除して古い API 定義が残る混乱を防ぐ。

## 現状

- `src/webtransport/quic.pyi` / `src/webtransport/http2.pyi` / `src/webtransport/http3.pyi` はパッケージ版 (quic/__init__.pyi 等) と実質重複し、import 解決ではパッケージディレクトリが優先されるため実質未使用
- パッケージ版にしか存在しない API (quic の offset / 接続統計 / initiate_key_update / extend_max_*、http2 の terminate_session / submit_trailer / remote_settings、http3 の submit_trailers / stream_priority / parse_priority 等) が欠落した古い内容のまま
- CHANGES.md に記載済みの機能が古いスタブでは解決できない
- h2.pyi / h3.pyi はパッケージ版が無いため現役 (削除対象外)

## 設計方針

- `src/webtransport/quic.pyi` / `http2.pyi` / `http3.pyi` を削除する
- パッケージ版の型スタブに欠落がある場合は別途対応する (本 issue の対象外)

## 完了条件

- 3 ファイルが削除され、型スタブの解決がパッケージ版に統一される
- ty / ruff チェックが通る
