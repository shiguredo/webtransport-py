# C コールバック境界の例外安全を横断的に確保し方針を文書化する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-c-callback-noexcept
- Polished: {YYYY-MM-DD}

## 目的

公開 API から Python コールバックを渡せるのは現状 `quic.Config.verify_callback` のみであり、他 C コールバック (ngtcp2 / nghttp3 / nghttp2 層の約 20 件) は Python 例外を送出し得ない。将来 Python コールバックが追加された際に C フレーム巻き戻しによる abort を起こさないよう、全 C コールバックに `noexcept` を付与し、境界方針を文書化する。issue 0147 (verify 単体の防御) から分離した横断作業である。

## 現状

- `src/bindings/quic.h` の static コールバック群に `noexcept` 指定は無い
- `src/bindings/http2.cpp` / `src/bindings/http3.cpp` / `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h3.cpp` の各コールバックにも `noexcept` 指定は無い
- コールバック境界の例外方針を述べた文書は CODEBASE.md / README のいずれにも無い

## 設計方針

- 全 C コールバックに `noexcept` を付与する。Python を呼ばないコールバックは動作不変の注釈変更に留まる
- 例外を送出し得る将来のコールバックは、外周で捕捉して `NGTCP2_ERR_CALLBACK_FAILURE` / `NGHTTP3_ERR_CALLBACK_FAILURE` / `NGHTTP2_ERR_CALLBACK_FAILURE` 相当を返す方針を CODEBASE.md に明記する
- 変更対象は上記バインディング層のみとし、振る舞い変更を伴わないことをテストで確認する

## 完了条件

- 全 C コールバックに `noexcept` が付与されていること
- CODEBASE.md に境界方針が明記されていること
- 既存のテスト全 834 件が引き続き通過すること
