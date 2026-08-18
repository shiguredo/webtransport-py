# HTTP/2 クライアントのリクエストボディがワイヤに送出されない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-request-body-not-sent
- Polished: 2026-08-18

## 目的

HTTP/2 クライアント (`http2.Connection`) で `submit_request` の後に `send_data` を呼んでも、リクエストボディが DATA フレームとして送出されない問題を修正する。現在は POST リクエストのボディがワイヤ上で消失し、サーバーに届かない。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection::submit_request` は `nghttp2_submit_request` を data provider を渡さずに呼んでいる (最後の 2 引数が `nullptr`)
- nghttp2 v1.70.0 の `set_request_flags` は data provider が NULL のとき `NGHTTP2_FLAG_END_STREAM` を立てるため、リクエスト HEADERS に END_STREAM が付き、ローカル側が half-closed になる
- 以後 `Http2Connection::send_data` が積んだバッファは `nghttp2_session_resume_data` が無効で DATA フレームが一切送出されない (実測確認済み: ワイヤ出力が SETTINGS と HEADERS (flags=0x5) のみで DATA 0 件)
- 高レベル層 `src/webtransport/http2/client.py` の `Client.send_data` も同様に無音で消える
- サーバー側 `submit_response` は `data_source_read_callback` を data provider として渡しており、クライアント側だけが欠落している

## 設計方針

- `Http2Connection::submit_request` に `data_source_read_callback` を data provider として渡す (`submit_response` と同様の構成)
- **ボディなしリクエスト (GET 等) の終端** は高レベル層で実現する。`data_source_read_callback` は既存ロジックを変更しない (空バッファなら `NGHTTP2_ERR_DEFERRED`、トレーラ処理は現状のまま)。理由: `data_source_read_callback` はサーバー側 `submit_response` と共有されており、空バッファで EOF を立てる変更はレスポンスの chunked 送信 (`submit_response` → `send_data` 後の DEFERRED → トレーラ) を壊すため
- **高レベル層 `src/webtransport/http2/client.py` の `Client.request`** を、リクエストを完結させる形に変更する。現状の `request(method, path, headers)` は submit_request 直後に `_send_pending()` で即 flush するため、data provider を常に渡すとボディなし GET の終端が失われる。そこで `request` にボディ引数 (例: `body: bytes | None = None`) を追加し、`request` 内で `submit_request` → (ボディがあれば) `send_data(body, eof=True)` / (ボディがなければ) 空データ + eof で終端、→ flush を一連で行う
- 既存の `request()` → `send_data()` 分離フロー (request でヘッダーのみ送信し、後から send_data でボディを送る) は、`request` が終端まで担う形に変わるため後方互換性が失われる。この契約変更は CHANGES.md に [CHANGE] として明記する
- 低レベル層の終端は `send_data(stream_id, data, eof=True)` の明示 eof で行う。なお、data provider を常時渡すと **低レベル API でも `submit_request` 単体では HEADERS に END_STREAM が付かなくなる** (現在は data provider 未設定のため自動で END_STREAM が付与され、submit_request 単体でリクエストが完結していた)。この低レベル API の動作変更も CHANGES.md の [CHANGE] として明記する
- 既存テスト `tests/test_http2_session_state.py` の「データプロバイダなしで submit_request し HEADERS に END_STREAM が付く」前提は、data provider 常時渡しにより崩れるため、`send_data(..., eof=True)` による明示終端に合わせて更新する。崩れるのはテスト後半 (レスポンス eof 送信後のストリーム消滅検証を含む) まで含めてテスト全体である
- `tests/prop_http2_roundtrip.py` の「データプロバイダーが未設定のため実際の DATA フレームは送信されない」という前提コメントは本修正で陳腐化するため、コメントの更新または送出検証への強化を行う
- リクエストトレーラ (`submit_trailer`) はサーバー限定のまま据え置く。closed issue 0022 では「クライアントはボディ送信不可のため除外」としていたが、本修正でボディ送信可能になるため、**トレーラ対象外の理由は改めて「本 issue のスコープ外 (リクエストトレーラの送信は未対応のまま)」と整理する**
- リクエストボディの送出を検証するテストを追加する。e2e テスト (`tests/test_e2e_http2.py` に POST テストが存在しない) に加え、低レベル層のテスト (`tests/test_http2.py` で `submit_request` → `send_data` → サーバー側 DATA イベントの確認) も追加する

## 完了条件

- `submit_request` → `send_data` の順で呼んだときに DATA フレームがワイヤに送出され、サーバー側でボディを受信できる
- ボディなしリクエストが `Client.request` で終端され、ストリームが残留しない (既存 GET e2e が通る)
- `Client.request` でボディ付きリクエスト (POST) が送出される
- 既存テスト (ボディなしリクエストの状態遷移を含む) が更新され通る
- 変更内容を CHANGES.md の `## develop` に記載する ([CHANGE] でリクエスト API の契約変更を明記)
