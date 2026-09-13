# WebTransport over HTTP/2 のエラーコード (WT_FLOW_CONTROL_ERROR 等) を定数として公開しマジックナンバー散在を解消する

- Created: 2026-09-07
- Completed: 2026-09-13
- Branch: feature/refactor-h2-wt-error-codes-constants
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 の WT_ERROR / WT_STREAM_STATE_ERROR / WT_FLOW_CONTROL_ERROR は現状 0xTBD のプレースホルダ (実装ではそれぞれ 0x52 / 0x51 / 0x50 の暫定値) だが、C++ / Python の 3 層に整数リテラルとして散在している。C++ で `kWtFlowControlError = 0x50` (webtransport_h2.cpp 内)、Python 高レベルで `event.error_code == 0x50` (h2/client.py / h2/server.py)、テストで `_WT_ERROR = 0x52` などの独自定義が 13 ファイルにある。draft で値が確定した際に一括更新できるよう定数として公開する。加えて `webtransport_h2.cpp` の `kWtStreamStateError` は関数内 `constexpr` で位置も不揃い。

## 現状

- `src/bindings/webtransport_h2.cpp` に `constexpr uint32_t kWtFlowControlError = 0x50;` (ファイルスコープ)、`constexpr uint32_t kWtError = 0x52;` (ファイルスコープ)、`kWtStreamStateError = 0x51` は `H2Session::report_stream_state_error` 内 `constexpr` (関数スコープ)
- `src/webtransport/h2/client.py` と `src/webtransport/h2/server.py` の `on_error` 分岐が `event.error_code == 0x50` をマジックナンバーでハードコード
- `src/bindings/webtransport_h2.h` にエラーコード定数の宣言が無い (バインディングで公開されていない)
- `tests/test_webtransport_h2_*.py` 13 ファイルの多くが `_WT_ERROR = 0x52` / `_WT_FLOW_CONTROL_ERROR = 0x50` 等を独自定義
- `src/bindings/webtransport_h3.h` にも同種のエラーコード (draft-16 Section 9.5) があるが、こちらも定数として公開されていない
- README / SKILL.md にエラーコードの説明無し

## 設計方針

- `webtransport_h2` サブモジュールに `ErrorCode` 相当の enum / モジュール定数を追加し、nanobind で公開する: `WT_ERROR = 0x52`、`WT_STREAM_STATE_ERROR = 0x51`、`WT_FLOW_CONTROL_ERROR = 0x50`
- C++ 側は `kWt*Error` を統一場所 (`webtransport_h2.h` 内の名前空間 or 定数ファイル) に集約する
- Python 高レベル (`h2/client.py` / `h2/server.py`) はマジックナンバーを import した定数に置き換える
- テストの独自定義を削除し、公開定数を import する
- 「draft で値が確定したら更新する」注記を統一場所 (1 箇所) にまとめる
- draft-16 側の `webtransport_h3` のエラーコード (draft-16 Section 9.5) は別 issue または本 issue の後段で対応する
- SKILL.md にエラーコード一覧と `on_error` の使い方を記載する

## 完了条件

- `webtransport.h2.WT_FLOW_CONTROL_ERROR` (等) が Python から参照できること
- C++ の `kWt*Error` 定数がヘッダに集約されていること
- Python 高レベル / tests のマジックナンバーが定数に置き換わっていること
- SKILL.md にエラーコードと `on_error` の説明があること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

- `src/bindings/webtransport_h2.h` に `enum WtErrorCode : uint32_t` を追加し、`kWtFlowControlError = 0x50` / `kWtStreamStateError = 0x51` / `kWtError = 0x52` を集約した。関数スコープにあった `kWtStreamStateError` とファイルスコープの 2 定数は削除し、`report_stream_state_error` / `report_recv_flow_control_error` / `report_wt_error` はこの enum を参照する
- `webtransport_h2.cpp` のバインディングで `h2.WtErrorCode` として公開し、`src/webtransport/h2/__init__.py` から再エクスポートした
- `h2.Server.on_error` / `h2.Client.on_error` のマジックナンバー (`event.error_code == 0x50`) を `h2_low.WtErrorCode.WT_FLOW_CONTROL_ERROR.value` に置き換えた
- tests の 12 ファイルに散在していた `0x50` / `0x51` / `0x52` とローカル定数 (`_WT_ERROR` など) を、`WtErrorCode` から導出するモジュール定数 (`WT_ERROR = WtErrorCode.WT_ERROR.value` など) に統一した。`h2.Event.error_code` は plain int で届くため `.value` を取る
- 「draft で値が確定したら更新する」注記は `WtErrorCode` の宣言コメント 1 箇所にまとめた
- `skills/webtransport-py/SKILL.md` にエラーコード一覧と `on_error` の使い方を追記した
- draft-16 側 (`webtransport_h3`) のエラーコードは本 issue の範囲外とした
- 全 1116 テストが通ることを確認した
