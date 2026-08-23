/**
 * WebTransport over HTTP/3 バインディング
 *
 * Sans-IO スタイルの WebTransport over HTTP/3 実装
 * HTTP/3 セッション上で WebTransport セッションを管理
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include <nghttp3/nghttp3.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace h3 {

/**
 * WebTransport セッション設定
 */
struct H3SessionConfig {
  // 最大フィールドセクションサイズ
  uint64_t max_field_section_size = 65536;

  // QPACK 設定
  uint64_t qpack_max_dtable_capacity = 4096;
  uint64_t qpack_blocked_streams = 100;

  // サーバーモードかどうか
  bool is_server = false;

  // 許可オリジン (サーバー用。空なら全オリジンを受理する)
  std::vector<std::string> allowed_origins;
};

/**
 * WebTransport イベント種別
 */
enum class H3EventType {
  // セッション関連
  SessionReady,
  SessionClosed,

  // ストリーム関連
  StreamOpened,
  StreamData,
  StreamClosed,

  // nghttp3 が QUIC 層に RESET_STREAM / STOP_SENDING の送出を要求する
  ResetStream,
  StopSending,

  // データグラム
  Datagram,

  // エラー
  Error,
};

/**
 * WebTransport イベント
 */
struct H3Event {
  H3EventType type;
  int64_t session_id = -1;
  int64_t stream_id = -1;
  std::vector<uint8_t> data;
  uint64_t error_code = 0;
  std::string error_message;
  bool is_unidirectional = false;
};

/**
 * WebTransport ストリーム情報
 */
struct StreamInfo {
  int64_t stream_id;
  int64_t session_id;
  bool is_unidirectional;
  bool is_incoming;
  // nghttp3 に書き込み用として登録済みかどうか
  bool is_write_registered;
};

/**
 * 送信待ちストリームデータ
 */
struct PendingData {
  int64_t stream_id;
  std::vector<uint8_t> data;
  bool fin;
};

/**
 * WebTransport over HTTP/3 セッション (Sans-IO)
 *
 * HTTP/3 コネクション上で WebTransport セッションを管理
 * QUIC ストリーム/データグラムは Python 側で処理
 */
class H3Session {
 public:
  /**
   * クライアントセッションを作成
   */
  static std::unique_ptr<H3Session> create_client(
      const H3SessionConfig& config);

  /**
   * サーバーセッションを作成
   */
  static std::unique_ptr<H3Session> create_server(
      const H3SessionConfig& config);

  ~H3Session();

  // コピー禁止
  H3Session(const H3Session&) = delete;
  H3Session& operator=(const H3Session&) = delete;

  // ムーブ許可
  H3Session(H3Session&&) noexcept;
  H3Session& operator=(H3Session&&) noexcept;

  /**
   * QUIC ストリームからデータを受信
   * @param stream_id ストリーム ID
   * @param data 受信データ
   * @param fin ストリーム終了フラグ
   * @return 処理されたバイト数
   */
  size_t receive_stream_data(int64_t stream_id,
                             const std::vector<uint8_t>& data,
                             bool fin = false);

  /**
   * QUIC データグラムを受信
   * @param data データグラムペイロード
   *
   * 先頭の Quarter Stream ID からセッション ID を復元する。QUIC ストリーム
   * ID の範囲を超えるセッション ID は H3_ID_ERROR で接続を閉じる
   * (draft-ietf-webtrans-http3-16 Section 4 の MUST)。
   *
   * 終了したセッション ID (close_stream による CONNECT ストリームのクローズ
   * / close_session / recv_wt_close_session_cb / クライアントでの非 2xx
   * 応答受信 / サーバー側の reject_session による非 2xx 拒否で session_ids_
   * から削除済み) と、一度も確立されていない
   * セッション ID 宛のデータグラムは破棄して Datagram イベントを発火しない。
   * 根拠は draft-ietf-webtrans-http3-16 Section 4 の「closed session 宛の
   * データの扱いは Section 6 に従う」と、データグラムは再送されず配信保証
   * がないこと (Section 4.1 / RFC 9221)。本対応は仕様の MUST 由来ではなく
   * 実装ポリシーである。受理前 FIN 検知済みセッション (終了を学習済みだが
   * close_stream による後始末前) 宛も破棄する。楽観的送受信は妨げない:
   * クライアントは connect 直後 (200 応答前)、サーバーは CONNECT リクエスト
   * 受信時 (end_headers_cb) に session_ids_ へ挿入される。ただしサーバー側
   * は CONNECT リクエストの処理完了前 (QPACK デコードブロック中を含む) に
   * 届いたデータグラムのみ破棄される (楽観的データグラムの先行到着で喪失
   * し得る。データグラムは再送されず喪失は無害なため許容)
   */
  void receive_datagram(const std::vector<uint8_t>& data);

  /**
   * 送信すべきストリームデータを取得
   * @return (stream_id, data, fin) のリスト
   */
  std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>>
  get_streams_to_send();

  /**
   * 送信すべきデータグラムを取得
   * @return データグラムのリスト
   */
  std::vector<std::vector<uint8_t>> get_datagrams_to_send();

  /**
   * QUIC コントロールストリーム ID を設定
   */
  void bind_control_stream(int64_t stream_id);

  /**
   * QPACK エンコーダーストリーム ID を設定
   */
  void bind_qpack_encoder_stream(int64_t stream_id);

  /**
   * QPACK デコーダーストリーム ID を設定
   */
  void bind_qpack_decoder_stream(int64_t stream_id);

  /**
   * WebTransport セッションを開始 (クライアント用)
   * CONNECT リクエストを送信
   * @param stream_id リクエストストリーム ID
   * @param url 接続先 URL (例: "https://example.com/path")
   * @param origin Origin ヘッダー値 (空なら付与しない)
   * @return 成功したかどうか
   */
  bool connect(int64_t stream_id,
               const std::string& url,
               const std::string& origin = "");

  /**
   * WebTransport セッションを受理 (サーバー用)
   *
   * 受理前に CONNECT ストリームが FIN で閉じられていた場合 (受理前 FIN。
   * クライアントが CONNECT 直後に FIN を送った場合に発生し得る) は、
   * 2xx レスポンスの書き出し完了後に自動でセッション終了処理を行い、
   * SessionClosed イベント (error_code 0) を発火する。2xx の書き出しが
   * 完了しない間 (フロー制御等) は終了処理が保留され、セッション ID が
   * session_ids_ に残る (未送信の 2xx を破棄しないための遅延クローズ)。
   * 保留中も送信は send_datagram / open_stream で拒否される。
   *
   * 受理前にピアが送った WT_CLOSE_SESSION カプセル (楽観的送信。
   * draft-ietf-webtrans-http3-16 Section 3.2) は nghttp3 の inq に
   * バッファされ、confirm の処理中に同期処理される (nghttp3 の実装順序に
   * 依存する挙動)。このときセッション終了が検知され、終了済みセッションの
   * 未送信 2xx は破棄される (SessionClosed は 1 回だけ発火する)。
   *
   * reject_session (非 2xx 拒否) で session_ids_ から削除済みのセッション
   * に対して呼んだ場合は false を返す (受理不可能の明示)。非 2xx で拒否
   * されたセッションは一度も確立されていない (draft-ietf-webtrans-http3-16
   * Section 3.2 の「サーバーの視点では、2xx 応答を送信した時点でセッション
   * が確立される」) ため、誤用経路で submit / confirm に進むと受理前に
   * バッファされた WT_CLOSE_SESSION カプセルが処理されて SessionClosed が
   * 発火し、終了通知の意味論に反する。false を返すことで誤用経路は
   * submit / confirm に進まず、安全側で失敗する。
   * @param stream_id セッションストリーム ID
   * @return 成功したかどうか
   */
  bool accept_session(int64_t stream_id);

  /**
   * WebTransport セッションを拒否 (サーバー用)
   *
   * 非 2xx 応答で拒否されたセッションは一度も確立されていない
   * (draft-ietf-webtrans-http3-16 Section 3.2 の「サーバーの視点では、2xx
   * 応答を送信した時点でセッションが確立される」) ため、session_ids_ から
   * セッション ID を削除する (SessionClosed イベントは発火しない。黙って
   * 削除)。受理前 FIN 検知済みセッション (pending_pre_accept_fin_session_ids_)
   * のエントリも除去する。2xx を渡した場合は削除しない (2xx 送出は確立
   * 条件。accept_session は 200 固定のため、2xx 非 200 応答は本 API で生成
   * する)。accept_session で受理済みのセッションに呼んだ場合は未定義 (誤用)。
   * 非 2xx 拒否後に accept_session を呼んだ場合 (もう一つの誤用経路) は
   * false を返す (受理不可能の明示。詳細は accept_session の docstring を
   * 参照)。
   * @param stream_id セッションストリーム ID
   * @param status_code HTTP ステータスコード
   */
  void reject_session(int64_t stream_id, int status_code);

  /**
   * WebTransport ストリームを開く
   *
   * 終了したセッション ID では false を返す (draft-ietf-webtrans-http3-16
   * Section 6 の MUST 「セッション終了を学習したエンドポイントは、新しい
   * ストリームも開いてはならない (it MUST NOT send any new datagrams or
   * open any new streams)」)。セッション終了の検知は session_ids_ の
   * メンバーシップ確認で行い、受理前 FIN を検知したセッション (終了を
   * 学習済みだが close_stream による後始末前) も同様に拒否する。一度も
   * 確立されていないセッション ID でも false を返す (本ライブラリの
   * 意味論)。セッション確立前の楽観的オープン
   * (draft-ietf-webtrans-http3-16 Section 4) は妨げない: クライアントは
   * connect 直後に session_ids_ へ挿入されるため。サーバー側は受理前
   * (accept_session 前) の open_stream は nghttp3 の wt.session 未設定に
   * より元々失敗する (既存の制約)
   * @param session_id セッション ID
   * @param stream_id QUIC ストリーム ID
   * @param is_unidirectional 単方向ストリームかどうか
   * @return 成功したかどうか
   */
  bool open_stream(int64_t session_id,
                   int64_t stream_id,
                   bool is_unidirectional);

  /**
   * WebTransport ストリームにデータを送信
   *
   * stream_info_ に未登録のストリーム (セッション ID を復元できない) への
   * 送信と、受信済みの単方向ストリーム (クライアント起点 %4==2 / サーバー
   * 起点 %4==3) への送信は黙って無視する。
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_stream_data(int64_t stream_id,
                        const std::vector<uint8_t>& data,
                        bool fin = false);

  /**
   * WebTransport データグラムを送信
   *
   * session_ids_ に含まれないセッション ID (終了した・一度も確立されて
   * いないセッションの ID) への送信は黙って無視する。セッション終了の
   * 検知は session_ids_ のメンバーシップ確認で行い、セッション ID の
   * 削除経路 (close_stream による CONNECT ストリームのクローズ /
   * close_session / recv_wt_close_session_cb / クライアントでの非 2xx
   * 応答受信 / サーバー側の reject_session による非 2xx 拒否) がすべて
   * session_ids_ からセッション ID を削除することに
   * 依存する。加えて、受理前 FIN を検知したセッション (終了を学習済みだ
   * が close_stream による後始末前。この間は session_ids_ に含まれたまま)
   * への送信も黙って無視する。
   * 根拠は draft-ietf-webtrans-http3-16 Section 6 の MUST 「セッション
   * 終了を学習したエンドポイントは、新しいデータグラムを送信しては
   * ならない (it MUST NOT send any new datagrams or open any new
   * streams)」。セッション確立前の楽観的送信 (draft-ietf-webtrans-http3-16
   * Section 4) は妨げない: クライアントは connect 直後に、サーバーは
   * CONNECT リクエスト受信時 (end_headers_cb) に session_ids_ へ挿入される
   * @param session_id セッション ID
   * @param data データグラムペイロード
   */
  void send_datagram(int64_t session_id, const std::vector<uint8_t>& data);

  /**
   * WebTransport ストリームを閉じる (nghttp3 にクローズを通知)
   *
   * QUIC 側で RESET_STREAM を送った後、対向から RESET_STREAM を受信した
   * 後、または CONNECT ストリームの FIN 受信後 (受信側のストリームクローズ
   * の検知) に呼び出す。CONNECT ストリーム (セッション ID 自身) のリセット
   * とクリーンクローズ (FIN) はセッション終了の正当な経路であり
   * (draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目)、
   * セッションに属するストリームの後始末を行って SessionClosed イベントを
   * 発火する。SessionClosed イベントの error_code は、リセット経路では QUIC
   * STREAM_RESET のアプリエラーコード、FIN 経路では 0 (WT_CLOSE_SESSION 無し
   * のクリーンクローズは error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION
   * と等価。draft-ietf-webtrans-http3-16 Section 6) であり、error_message は
   * 空である。高レベル Server ではピア由来の CONNECT ストリームのリセット時に
   * on_stream_reset (セッション ID 付き) に続いて on_session_closed が発火する
   * (FIN 経路とローカル起因のリセットでは on_session_closed のみ)。FIN 経路の
   * 受信時に低レベル API (h3 層) は応答 FIN や RESET_STREAM を送出しない。
   * 高レベル Client では CONNECT ストリームの送信側が half-closed のままに
   * なり、ピアが完全クローズを待つ場合の相互運用に影響し得る (既知の制約。
   * 高レベル Server は SESSION_CLOSED ハンドラで QUIC 直接注入による応答 FIN
   * を送出する)
   * @param stream_id ストリーム ID
   * @param error_code エラーコード (QUIC STREAM_RESET のアプリエラーコード。
   *   FIN 経路では 0 を渡す)
   * @return クローズされたストリームが属するセッション ID。
   *   CONNECT ストリーム (セッション ID は CONNECT ストリーム ID そのもの。
   *   draft-ietf-webtrans-http3-16 Section 2.2) の場合は 1 回目のクローズでは
   *   ストリーム ID 自身を返す (セッション終了後は session_ids_ から削除される
   *   ため、2 回目以降は -1 を返す)。クライアントが非 2xx 応答を受信した
   *   セッション (確立されず session_ids_ から削除済み) と、サーバー側の
   *   reject_session (非 2xx 拒否) で拒否されたセッションの CONNECT ストリーム
   *   は 1 回目から -1 を返す。
   *   セッション ID を復元できない場合 (制御ストリーム・QPACK ストリーム・
   *   WT ヘッダー未受信のままリセットされたストリーム等) は -1 を返す。
   *   コネクションが無い場合も -1 を返す。
   */
  int64_t close_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * WebTransport ストリームをリセットする
   *
   * nghttp3 への通知は close_stream と同じ。close_stream の戻り値
   * (セッション ID) は破棄する。高レベル API では QUIC RESET_STREAM
   * 送出と合わせて使う。
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void reset_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * WebTransport セッションを閉じる
   * @param session_id セッション ID
   * @param error_code エラーコード
   * @param error_message エラーメッセージ
   */
  void close_session(int64_t session_id,
                     uint64_t error_code = 0,
                     const std::string& error_message = "");

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<H3Event> next_event();

  /**
   * 必要な QUIC ストリーム ID のリストを取得
   * @return 必要なストリームの種類と方向のリスト
   */
  std::vector<std::pair<std::string, bool>> get_required_streams() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

  /**
   * 確立されたセッション ID のリストを取得
   */
  std::vector<int64_t> get_session_ids() const;

  /**
   * セッションに属するストリームを取得
   */
  std::vector<StreamInfo> get_session_streams(int64_t session_id) const;

  /**
   * クライアントからの双方向ストリームの最大数を設定 (サーバー用)
   * リクエストストリームを受け入れる前に呼び出す必要がある
   * @param max_streams 最大ストリーム数
   */
  void set_max_client_streams_bidi(uint64_t max_streams);

  /**
   * ストリームの QUIC フロー制御ブロックを通知
   *
   * ストリームが QUIC フロー制御でブロックされたことを nghttp3 に伝え、
   * スケジューラから外す。クライアント双方向ストリーム (% 4 == 0) のみ
   * 即時にスケジューラから外れ、単方向ストリームはスケジューラから
   * 外れないためブロック直後に 1 回の書き込みが通る。
   * 存在しないストリームは no-op。
   * @param stream_id ストリーム ID
   */
  void block_stream(int64_t stream_id);

  /**
   * ストリームの QUIC フロー制御ブロック解除を通知
   *
   * block_stream でブロックしたストリームの解除を nghttp3 に伝え、
   * スケジューリングを再開する。GOAWAY 受信 (graceful shutdown) 後も
   * 既存ストリームのフロー制御ブロック操作は有効なため、closed_ は
   * 見ない (H3Session の閉鎖は QUIC コネクション層が担う)
   * @param stream_id ストリーム ID
   * @return 成功したかどうか (存在しないストリームは成功扱い。メモリ
   *  不足の場合のみ false。コネクションが無い場合は false)
   */
  bool unblock_stream(int64_t stream_id);

  /**
   * 同時ストリーム数のヒントを設定
   *
   * QPACK デコーダーの内部リソース消費のヒント (decoder stream の長さ
   * 制限)。現在値との max マージのため、小さい値は反映されない
   * (nghttp3 内部の実効下限は 100)。
   * @param n 同時ストリーム数のヒント
   */
  void max_concurrent_streams(size_t n);

  /**
   * テスト専用: ストリームの送信バッファエントリが存在するか
   *
   * 恒久的な公開 API ではなく、テストでのバッファ解放検証にのみ使う。
   * @param stream_id ストリーム ID
   * @return エントリが存在する場合は true、存在しない場合は nullopt
   */
  std::optional<bool> has_stream_buffer(int64_t stream_id) const;

  /**
   * テスト専用: QPACK デコードブロック中 fin の保留記録に含まれるか
   *
   * 恒久的な公開 API ではなく、テストでの記録・除去の検証にのみ使う。
   * @param stream_id ストリーム ID
   * @return 記録に含まれる場合は true、含まれない場合は nullopt
   */
  std::optional<bool> has_pending_qpack_blocked_fin_stream(
      int64_t stream_id) const;

  /**
   * テスト専用: reject_session が最後に送出した応答のステータスコード
   *
   * 恒久的な公開 API ではなく、テストでの拒否応答ステータスの検証にのみ
   * 使う。reject_session 未呼び出し時 (ガードによる no-op を含む) は nullopt。
   * @return 最後に送出したステータスコード
   */
  std::optional<int64_t> last_reject_status_code() const;

  /**
   * ストリームが書き込み可能か確認
   *
   * 存在しない・closed・フロー制御ブロック・入力データ待ち・half-closed の
   * いずれかで書き込み不可。
   * @param stream_id ストリーム ID
   * @return 書き込み可能なら 1、不可なら 0、コネクションが無いか閉じている
   *   場合は nullopt
   */
  std::optional<int> stream_writable(int64_t stream_id) const;

  /**
   * ストリームの全送信データが QUIC スタックに受け渡し済みか確認
   *
   * write offset ベースの判定であり、ACK は考慮しない。新たに送信した
   * データは get_streams_to_send() による送信処理の後に反映される。
   * 存在しないストリームは受け渡し済み扱い (1) になる。
   * @param stream_id ストリーム ID
   * @return 受け渡し済みなら 1、未了なら 0、コネクションが無いか閉じている
   *   場合は nullopt
   */
  std::optional<int> stream_flushed(int64_t stream_id) const;

  /**
   * ストリームが属する WebTransport セッション ID を取得
   *
   * WebTransport データストリーム以外 (CONNECT ストリーム自身・制御
   * ストリーム・QPACK ストリーム) はセッション ID を持たない。
   * @param stream_id ストリーム ID
   * @return セッション ID。ストリームが存在しない場合、WebTransport
   *   データストリームでない場合、コネクションが無いか閉じている場合は
   *   nullopt
   */
  std::optional<int64_t> stream_wt_session_id(int64_t stream_id) const;

  /**
   * リクエストヘッダーの Origin を検証する (サーバー用)
   *
   * 許可オリジンリスト (allowed_origins) が空 (未設定) の場合は常に受理し、
   * Origin ヘッダーが無いリクエストも受理する (仕様上 Origin は非ブラウザ
   * クライアントでは OPTIONAL)。Origin ヘッダーが複数ある場合、値が空の
   * 場合、許可リストと一致しない場合は拒否する。照合はバイト列の完全一致
   * であり、RFC 6454 の origin 正規化 (デフォルトポートの省略やホスト名の
   * 大文字小文字) は行わない。
   * @param headers 受信したリクエストヘッダー
   * @return 受理してよい場合は true
   */
  bool verify_origin(
      const std::vector<std::pair<std::string, std::string>>& headers) const;

  /**
   * nghttp3 の read_data コールバックから呼ばれる
   * stream_buffers_ からデータを取得して返す
   */
  nghttp3_ssize read_data_callback(int64_t stream_id,
                                   nghttp3_vec* vec,
                                   size_t veccnt,
                                   uint32_t* pflags);

 private:
  H3Session(bool is_server, const H3SessionConfig& config);

  bool initialize();

  // nghttp3 コールバック
  static int acked_stream_data_cb(nghttp3_conn* conn,
                                  int64_t stream_id,
                                  uint64_t datalen,
                                  void* conn_user_data,
                                  void* stream_user_data);
  static int stream_close_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             uint64_t app_error_code,
                             void* conn_user_data,
                             void* stream_user_data);
  static int recv_data_cb(nghttp3_conn* conn,
                          int64_t stream_id,
                          const uint8_t* data,
                          size_t datalen,
                          void* conn_user_data,
                          void* stream_user_data);
  static int deferred_consume_cb(nghttp3_conn* conn,
                                 int64_t stream_id,
                                 size_t consumed,
                                 void* conn_user_data,
                                 void* stream_user_data);
  static int begin_headers_cb(nghttp3_conn* conn,
                              int64_t stream_id,
                              void* conn_user_data,
                              void* stream_user_data);
  static int recv_header_cb(nghttp3_conn* conn,
                            int64_t stream_id,
                            int32_t token,
                            nghttp3_rcbuf* name,
                            nghttp3_rcbuf* value,
                            uint8_t flags,
                            void* conn_user_data,
                            void* stream_user_data);
  static int end_headers_cb(nghttp3_conn* conn,
                            int64_t stream_id,
                            int fin,
                            void* conn_user_data,
                            void* stream_user_data);
  static int end_stream_cb(nghttp3_conn* conn,
                           int64_t stream_id,
                           void* conn_user_data,
                           void* stream_user_data);
  static int stop_sending_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             uint64_t app_error_code,
                             void* conn_user_data,
                             void* stream_user_data);
  static int reset_stream_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             uint64_t app_error_code,
                             void* conn_user_data,
                             void* stream_user_data);
  static int shutdown_cb(nghttp3_conn* conn, int64_t id, void* conn_user_data);
  static int recv_settings2_cb(nghttp3_conn* conn,
                               const nghttp3_proto_settings* settings,
                               void* conn_user_data);
  static int recv_wt_data_cb(nghttp3_conn* conn,
                             int64_t session_id,
                             int64_t stream_id,
                             const uint8_t* data,
                             size_t datalen,
                             void* conn_user_data,
                             void* stream_user_data);
  // 対向からの WT_CLOSE_SESSION カプセル受信
  static int recv_wt_close_session_cb(nghttp3_conn* conn,
                                      int64_t session_id,
                                      uint32_t wt_error_code,
                                      const uint8_t* msg,
                                      size_t msglen,
                                      void* conn_user_data,
                                      void* stream_user_data);

  // ヘルパー
  void push_event(H3Event event);

  // セッションに属するストリームの送信バッファとストリーム情報を削除する
  // close_session / recv_wt_close_session_cb / close_stream (CONNECT ストリーム
  // のクローズ経路。リセットと FIN の両方) から呼ばれる
  void erase_session_streams(int64_t session_id);

  // 遅延クローズ保留中に WT_CLOSE_SESSION を受信したセッションの未送信
  // 2xx を破棄する (pending_stale_2xx_discard_session_ids_ の処理)。
  // receive_stream_data が nghttp3_conn_read_stream2 から戻った後 (再入防止
  // のためコールバック内では実行できない) と、accept_session が confirm から
  // 戻った後 (アプリ呼び出しのため直接実行できる) の両方から呼ばれる
  void discard_stale_2xx();

  bool is_server_;
  H3SessionConfig config_;
  nghttp3_conn* conn_ = nullptr;

  // イベントキュー
  std::deque<H3Event> events_;

  // ストリームデータ (送信待ち)
  struct StreamBuffer {
    std::vector<uint8_t> data;
    // nghttp3 に渡済みのオフセット。未 ACK でも再送しないために進める
    size_t offset = 0;
    bool fin = false;
  };
  std::map<int64_t, std::deque<StreamBuffer>> stream_buffers_;

  // 送信待ちストリームデータ (nghttp3 から生成)
  std::deque<PendingData> pending_sends_;

  // 送信待ちデータグラム
  std::deque<std::vector<uint8_t>> pending_datagrams_;

  // 現在受信中のヘッダー
  std::map<int64_t, std::vector<std::pair<std::string, std::string>>>
      pending_headers_;

  // WebTransport セッション管理
  std::set<int64_t> session_ids_;

  // end_stream コールバックで FIN を検知した CONNECT ストリームのセッション
  // ID (セッション終了の後始末の保留集合)。コールバック内では nghttp3 を
  // 再度呼ばず (再入防止)、receive_stream_data が nghttp3_conn_read_stream2
  // から戻った後に close_stream で処理する。1 回の read_stream2 呼び出しで
  // 処理されるストリームは 1 つだけのため、実際に入るのは高々 1 件
  std::set<int64_t> pending_fin_session_ids_;

  // receive_stream_data の fin 引数で検知した受理前 FIN (サーバーが応答を
  // 送信する前に CONNECT ストリームが FIN で閉じられた) のセッション ID。
  // 受理前 FIN では end_stream コールバックが発火しないため fin 引数で
  // 直接検知する (発生理由の詳細は receive_stream_data の実装コメント)。
  // accept_session による受理と 2xx レスポンスの書き出し完了後に
  // close_stream で後始末する
  std::set<int64_t> pending_pre_accept_fin_session_ids_;

  // QPACK デコードブロック中に fin を検知した CONNECT ストリーム候補の
  // ストリーム ID。ヘッダー未処理 (begin_headers_cb 発火済み・end_headers_cb
  // 未発火) のためセッション ID に確定していない。receive_stream_data の
  // fin 引数で記録し、ブロック解除後の読み取りで CONNECT 判定 (session_ids_
  // への挿入) を確認して pending_pre_accept_fin_session_ids_ へ移行する
  // (詳細は receive_stream_data の実装コメント)。セッション確定に至らな
  // かったストリーム (非 CONNECT・Origin 検証失敗を含む) は end_headers_cb
  // で、ブロック中にリセットされたストリームは close_stream で除去する
  std::set<int64_t> pending_qpack_blocked_fin_stream_ids_;

  // 受理前 FIN を検知したセッションのうち、accept_session で受理済みの
  // セッション ID。2xx レスポンスの書き出し完了 (stream_flushed) を
  // 確認してから close_stream で後始末する
  std::set<int64_t> pre_accept_fin_accepted_session_ids_;

  // 遅延クローズ保留中 (pre_accept_fin_accepted_session_ids_ に含まれる)
  // に WT_CLOSE_SESSION を受信したセッション ID、および accept_session の
  // confirm 処理中 (accepting_session_id_ と一致) に発火したセッション ID。
  // どちらも未送信の 2xx を破棄するため close_stream を実行する必要がある
  // が、recv_wt_close_session_cb は nghttp3_conn_read_stream2 の処理中に
  // 同期発火するため、コールバック内で nghttp3 を呼ぶと再入になる。
  // receive_stream_data が read_stream2 から戻った後、または accept_session
  // が confirm から戻った後 (アプリ呼び出しのため直接実行できる) に
  // close_stream で破棄する (pending_fin_session_ids_ と同じパターン)
  std::set<int64_t> pending_stale_2xx_discard_session_ids_;

  // accept_session の confirm 処理中のセッション ID (処理中でなければ -1)。
  // confirm の処理中に発火する recv_wt_close_session_cb (受理前にバッファ
  // された WT_CLOSE_SESSION カプセルの同期処理) の破棄記録条件の判定に
  // 使う。受理前 FIN なしのセッションは pre_accept_fin_accepted_session_ids_
  // に含まれず、かつ 2xx は submit 済みで破棄対象になるため、この記録が
  // 無いと未送信 2xx が送出されてしまう
  int64_t accepting_session_id_ = -1;

  // ストリームとセッションのマッピング
  std::map<int64_t, StreamInfo> stream_info_;

  // 制御ストリーム ID
  int64_t control_stream_id_ = -1;
  int64_t qpack_encoder_stream_id_ = -1;
  int64_t qpack_decoder_stream_id_ = -1;

  // 接続状態
  bool closed_ = false;

  // reject_session が最後に送出した応答のステータスコード (テスト専用。
  // 未送出時は std::nullopt を返すために 0 で初期化する)
  int64_t last_reject_status_code_ = 0;
};

// Python バインディングを定義
void bind_webtransport_h3(nb::module_& m);

}  // namespace h3
}  // namespace webtransport
