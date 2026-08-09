# ハンドシェイク損失下でも接続が完了することを LossyRelay で検証する

- Created: 2026-08-09
- Completed: YYYY-MM-DD
- Branch: feature/add-lossy-relay-handshake-loss
- Polished: {YYYY-MM-DD}
- Reporter: @voluntas

## 目的

QUIC ハンドシェイクのパケット (Initial / Handshake) が UDP ロスした状況でも `quic.Client.connect()` がハンドシェイク完了まで到達する (再送で回復する) ことを、公開 API のみを使った e2e テストで検証できるようにする。実運用ではハンドシェイクロスは日常的に起きる (帯域制限・NAT の再バインド・混雑) が、現状の webtransport-py にはロス下のハンドシェイクを検証するテスト経路が無い。テストヘルパーとして「クライアントとサーバーの間に挟まる UDP リレー」を追加し、パケットのドロップ規則を注入できるようにする。

## 現状

- リポジトリ全体を `LossyRelay` / `lossy_relay` で grep しても 0 件。パケットロスを注入するテストヘルパーは存在しない
- 既存の再送テスト `tests/test_e2e_quic_advanced.py` の `test_early_data_rejected` は「0-RTT 拒否 → 開き直したストリームの再送」を検証するが、UDP ロスによるハンドシェイク再送は対象外
- `tests/conftest.py` は自己署名証明書生成のみを提供しており、リレーやフィルタリングの土台は存在しない
- 高レベル `quic.Client` は接続先アドレスを `(host, port)` で保持し (src/webtransport/quic/client.py `_host` / `_port`)、`_send_pending()` の宛先解決も既定でここへ落ちる (`_destination_for_packet`)。したがってリレー経由の接続はクライアントに「サーバー宛」としてリレーのアドレスを渡すことで成立する

## 設計方針

- テストヘルパー `tests/lossy_relay.py` (仮) に `LossyRelay` クラスを追加する。asyncio UDP を用いた「1 対 1 の UDP リレー」であり、クライアントから受け取ったパケットをサーバーへ、サーバーから受け取ったパケットをクライアントへ転送する。転送前にドロップ規則を評価して、ドロップ対象のパケットは転送しない
- ドロップ規則はコールバック関数として注入できるようにする (`drop_rule: Callable[[LossyRelayPacket], bool]`)。`LossyRelayPacket` は `direction`, `index`, `data` を保持する軽量 dataclass。方向は `"c2s"` / `"s2c"` の 2 値、`index` は方向別の 0 始まりカウンタ (再送でも順に +1)。データを覗く必要は基本的に無い (ハンドシェイクパケットの選別は先頭数バイトで可能だが、テストの意図は「何かをロスさせても回復する」ことなのでインデックスベースで十分)
- パケット改変は行わない (本 issue の目的はロスの再現のみ)。将来的な遅延・重複などの機能追加は本 issue の対象外とし、ドロップだけを実装する
- LossyRelay は asyncio コンテキストマネージャとして起動・停止する。`listen_port` は 0 で自動割り当てし、`actual_port` プロパティで取得できるようにする。クライアントの接続先アドレスとして `("127.0.0.1", relay.actual_port)` を渡す
- サーバー宛アドレスはコンストラクタで受け取る (`server_addr: tuple[str, int]`)。クライアント側のアドレスは最初にパケットを送ってきた `(host, port)` で確定する (単一クライアント前提)
- テストとして `tests/test_e2e_quic_advanced.py` に `test_handshake_completes_with_initial_packet_loss` を追加する。ドロップ規則は「c2s 方向の 0 番目 (最初のクライアント Initial) をドロップし、以降は全通し」とし、`await client.connect()` が True を返すこと (再送で回復すること) と、その後の 1 ストリーム往復が成立することを確認する
- 追加テストの粒度: 本 issue では上記 1 本のみを目標にする (「LossyRelay 経由でハンドシェイクが完了する」ことの回帰テスト最小構成)。Handshake 段の複数パケットロスやサーバー側の Initial ロスといったバリエーションは本 issue の対象外 (将来 issue に分けて追加を検討する)
- 使用するのは高レベル `quic.Client` / `quic.Server` の公開 API のみ (`connect`, `open_stream`, `send_stream_data`, `on_stream_data`, `close` など)。Sans I/O API へのフォールバックは行わない
- 変更対象は `tests/lossy_relay.py` (新規)、`tests/test_e2e_quic_advanced.py` (テスト 1 本追加)。プロダクトコード (`src/webtransport/**`) は変更しない
- ハンドシェイクのタイムアウト設計: `Client.connect(timeout=10.0)` の既定値で足りる想定 (ngtcp2 の Initial 再送タイマーは PTO 相当で秒オーダー)。再送が起きなかった場合は timeout でテストが失敗するため、実装で余裕を持たせる (テスト側で `timeout=15.0` 等を明示的に指定してよい)

## 完了条件

- `tests/lossy_relay.py` に `LossyRelay` クラスが追加され、asyncio コンテキストマネージャとして起動・停止できる
- `LossyRelay` は `drop_rule` コールバックで指定されたパケットを転送せずドロップし、それ以外はクライアント⇔サーバー間を透過的に転送する
- `tests/test_e2e_quic_advanced.py` に `test_handshake_completes_with_initial_packet_loss` (仮) が追加され、c2s の最初のパケットをドロップした状態で `await client.connect()` が True を返し、その後 1 ストリームのデータ往復が成立する
- 既存の全テストが通る (LossyRelay は既存テストのフィクスチャに組み込まない)

## 解決方法

(実装時に追記する)
