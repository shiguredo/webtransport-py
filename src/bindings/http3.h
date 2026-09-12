/**
 * HTTP/3 バインディング (nghttp3 ラッパー)
 *
 * Sans-IO スタイルの HTTP/3 実装
 * QUIC ストリーム上で動作。QUIC は別途管理。
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
namespace http3 {

/**
 * HTTP/3 設定
 */
struct Http3Config {
  // 最大フィールドセクションサイズ
  uint64_t max_field_section_size = 65536;

  // QPACK 設定
  uint64_t qpack_max_dtable_capacity = 4096;
  uint64_t qpack_blocked_streams = 100;

  // WebTransport 有効化
  bool enable_webtransport = false;

  // HTTP/3 Datagram 有効化
  bool enable_h3_datagram = false;

  // サーバーモード
  bool is_server = false;
};

/**
 * HTTP/3 イベント種別
 */
enum class Http3EventType {
  Headers,
  Data,
  StreamEnd,
  GoAway,
  // nghttp3 が QUIC 層への送出を要求する
  ResetStream,
  StopSending,
  // 1xx (interim response)。RFC 9114 Section 4.1
  Informational,
  // トレーラ (終端 HEADERS で :status を持たないもの)
  Trailers,
  // HTTP/3 プロトコルエラー (nghttp3 の負値 return)。
  // error_code は RFC 9114 Section 8.1 の H3 ワイヤーエラーコード
  Error,
};

/**
 * HTTP/3 イベント
 */
struct Http3Event {
  Http3EventType type;
  int64_t stream_id = -1;
  std::vector<std::pair<std::string, std::string>> headers;
  std::vector<uint8_t> data;
  uint64_t error_code = 0;
  int64_t push_id = -1;
  // Error イベントのエラーメッセージ (nghttp3_strerror の文字列)。
  // 他イベントでは空
  std::string error_message;
};

/**
 * ストリームデータ (送信キュー用)
 */
struct StreamData {
  std::vector<uint8_t> data;
  size_t offset = 0;
  bool fin = false;
};

/**
 * 送信待ちストリームデータ
 */
struct PendingStreamData {
  int64_t stream_id;
  std::vector<uint8_t> data;
  bool fin;
};

/**
 * HTTP/3 コネクション (Sans-IO)
 *
 * QUIC ストリーム上で動作。
 * QUIC コネクションは Python 側で管理し、ストリームデータを受け渡す。
 */
class Http3Connection {
 public:
  /**
   * クライアントとして接続を作成
   */
  static std::unique_ptr<Http3Connection> create_client(
      const Http3Config& config);

  /**
   * サーバーとして接続を作成
   */
  static std::unique_ptr<Http3Connection> create_server(
      const Http3Config& config);

  ~Http3Connection();

  // コピー禁止
  Http3Connection(const Http3Connection&) = delete;
  Http3Connection& operator=(const Http3Connection&) = delete;

  // ムーブ許可
  Http3Connection(Http3Connection&&) noexcept;
  Http3Connection& operator=(Http3Connection&&) noexcept;

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
   * 送信すべきストリームデータを取得
   * @return (stream_id, data, fin) のリスト
   */
  std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>>
  get_streams_to_send();

  /**
   * QUIC コントロールストリーム ID を設定
   * @param stream_id コントロールストリーム ID
   */
  void bind_control_stream(int64_t stream_id);

  /**
   * QPACK エンコーダーストリーム ID を設定
   * @param stream_id エンコーダーストリーム ID
   */
  void bind_qpack_encoder_stream(int64_t stream_id);

  /**
   * QPACK デコーダーストリーム ID を設定
   * @param stream_id デコーダーストリーム ID
   */
  void bind_qpack_decoder_stream(int64_t stream_id);

  /**
   * リクエストを送信 (クライアント用)
   * @param stream_id QUIC ストリーム ID
   * @param headers リクエストヘッダー
   * @return 成功したかどうか
   */
  bool submit_request(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * レスポンスを送信 (サーバー用)
   * @param stream_id ストリーム ID
   * @param headers レスポンスヘッダー
   * @return 成功したかどうか
   */
  bool submit_response(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * ストリームにデータを送信
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_data(int64_t stream_id,
                 const std::vector<uint8_t>& data,
                 bool fin = false);

  /**
   * ストリームをリセット
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  /**
   * ストリームの読み取りを停止してリセットを要求する
   *
   * error_code は QUIC RESET_STREAM に載るアプリケーションエラーコードで、
   * HTTP/3 のエラーコードに限らない (WebTransport データストリームは
   * WT_APPLICATION_ERROR レンジの値をそのまま載せる)。汎用 API のため
   * close_stream と異なり「状況に応じた HTTP/3 エラーコード」を既定値に
   * できない。既定の 0 はアプリケーション固有のエラーなしを意味する
   * (RFC 9114 Section 4.1.1 の cancel / reject を HTTP/3 として表明したい
   * 呼び出し側は H3_REQUEST_CANCELLED / H3_REQUEST_REJECTED を明示する)
   */
  void reset_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * テスト専用: 低レベルを閉鎖状態にする (production からは呼ばない)
   *
   * nghttp3 の read_stream2 / writev_stream が負値を返した経路
   * (`closed_ = true`) を Python から人工的に作る。イベントは push しない
   * ため、HTTP/3 プロトコルエラーで低レベルが自主クローズした状態と同じ
   * 観測になる
   */
  void test_force_close();

  /**
   * QUIC ストリーム終了を nghttp3 に通知する
   *
   * QUIC の STREAM_CLOSED を受けたときに呼ぶ。
   * nghttp3 の stream_close コールバック経由で STREAM_END イベントが生成される。
   *
   * @param stream_id ストリーム ID
   * @param error_code アプリケーションエラーコード
   */
  /**
   * QUIC ストリーム終了を nghttp3 に通知する
   *
   * @param stream_id ストリーム ID
   * @param error_code ストリームが終了した理由の HTTP/3 アプリケーション
   *   エラーコード (RFC 9114 Section 8.1)。省略時は H3_NO_ERROR (0x0100)。
   *   H3 のエラーコード空間では 0 は予約域 (0x0000-0x00ff) のため使わない
   */
  void close_stream(int64_t stream_id,
                    uint64_t error_code = NGHTTP3_H3_NO_ERROR);

  /**
   * GOAWAY を送信
   *
   * GOAWAY ID は nghttp3 が受信済み最大ストリームから自動計算するため、
   * 引数の id は無視される。二重呼び出しは黙って無視する (同値再送も
   * 抑止する)。
   * @param id GOAWAY ID (現状は無視される)
   */
  /**
   * GOAWAY を送信する
   *
   * GOAWAY ID は nghttp3 が内部で算出する (サーバーは受信済み最大双方向
   * ストリーム ID、クライアントは 0)。nghttp3 に ID を指定する API が無い
   * ため引数は取らない
   */
  void goaway();

  /**
   * トレーラを送信
   *
   * send_data(fin=True) で本体を積んだ後、flush 前に呼ぶこと。
   * 呼び出し自体がストリーム終端 (WRITE_END_STREAM) を担う。
   * flush で fin が送信処理された後に呼ぶと
   * NGHTTP3_ERR_INVALID_STATE になるため False を返す。
   * @param stream_id ストリーム ID
   * @param headers トレーラヘッダー
   * @return 成功したかどうか
   */
  bool submit_trailers(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * 1xx レスポンスを送信 (サーバー専用)
   *
   * 最終レスポンス (submit_response) より前に呼ぶこと。1xx は
   * frq の書き出し順で最終レスポンスより先に送られる。
   * @param stream_id ストリーム ID
   * @param headers レスポンスヘッダー (:status を含む)
   * @return 成功したかどうか
   */
  bool submit_info(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * graceful shutdown の開始通知を送信 (サーバー専用)
   *
   * ピアに新しいストリームの作成を止めるよう通知する (GOAWAY 相当)。
   * 通知後に nghttp3_conn_shutdown (goaway()) を呼ぶことで graceful
   * shutdown を完了させる。goaway() の後に呼ぶと GOAWAY ID の
   * 単調減少 (RFC 9114 5.2 節の MUST NOT) に違反するため False を返す。
   * @return 成功したかどうか
   */
  bool submit_shutdown_notice();

  /**
   * ストリームの書き込み側をシャットダウン
   *
   * QUIC FIN ではなく、以降の書き込みを禁止する。
   * シャットダウン後の send_data は no-op、submit_trailers は
   * False を返す。
   * @param stream_id ストリーム ID
   */
  void shutdown_stream_write(int64_t stream_id);

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<Http3Event> next_event();

  /**
   * 必要な QUIC ストリーム ID のリストを取得
   * HTTP/3 は特定のストリーム (control, qpack encoder/decoder) を必要とする
   * @return 必要なストリームの種類と方向のリスト
   */
  std::vector<std::pair<std::string, bool>> get_required_streams() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

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
   * ストリームの送信バッファエントリの有無を確認 (テスト専用)
   *
   * @param stream_id ストリーム ID
   * @return エントリがあれば true、なければ nullopt
   */
  std::optional<bool> has_stream_buffer(int64_t stream_id) const;

  /**
   * 受信中フレームのペイロード残量を取得
   *
   * クライアント双方向ストリームまたはリモート制御ストリーム以外は 0。
   * 負の stream_id と varint 最大値 (2**62 - 1) を超える stream_id は
   * nghttp3 の assert を避けるため 0 を返す。
   * @param stream_id ストリーム ID
   * @return 残量。コネクションが無いか閉じている場合は nullopt
   */
  std::optional<uint64_t> frame_payload_left(int64_t stream_id) const;

  /**
   * ドレイン状態か確認 (サーバー専用)
   *
   * goaway() を呼び、アクティブなリモート双方向ストリームが無く、
   * GOAWAY フレームの書き出し (送信処理) が完了している場合に true。
   * @return ドレイン状態なら true、それ以外は false、クライアント
   *   またはコネクションが無いか閉じている場合は nullopt
   */
  std::optional<bool> drained() const;

  /**
   * ストリームの優先度を取得 (サーバー専用)
   *
   * クライアントから Priority ヘッダーや PRIORITY_UPDATE フレームで
   * 設定された優先度を返す。設定されていない場合は nghttp3 の
   * 内部デフォルト (urgency=3 / incremental=false) を返す。
   * @param stream_id クライアント起動双方向ストリーム ID
   * @return (urgency, incremental) のタプル。クライアントで呼び出した
   *   場合・クライアント起動双方向でないストリーム・存在しない
   *   ストリーム・範囲外の stream_id・コネクションが無いか閉じている
   *   場合は nullopt
   */
  std::optional<std::pair<uint32_t, bool>> stream_priority(
      int64_t stream_id) const;

  /**
   * クライアントからの双方向ストリームの最大数を設定 (サーバー専用)
   *
   * QUIC 層が MAX_STREAMS で許可するクライアント起動双方向ストリームの
   * 累積数を nghttp3 に伝える。設定しない場合、PRIORITY_UPDATE フレーム
   * が H3_ID_ERROR で拒否される。累積最大数は単調増加のみ許可され、
   * 減らす呼び出しは nghttp3 の assert に違反する (依存 3 ライブラリは
   * Release ビルドでも -DNDEBUG が除去され assert が本番でも有効なため、
   * C++ 側で減算を防ぐ)。
   * @param max_streams クライアント起動双方向ストリームの累積最大数
   */
  void set_max_client_streams_bidi(uint64_t max_streams);

  /**
   * クライアント起動双方向ストリームの優先度を設定 (クライアント専用)
   *
   * PRIORITY_UPDATE フレームを送信して優先度を通知する。
   * 送信するだけであり、クライアント自身の送信順序には反映されない
   * (反映されるのはサーバー側のスケジューリング)。
   * @param stream_id クライアント起動双方向ストリーム ID
   * @param urgency 優先度 (0-7。0 が最高)
   * @param incremental インクリメンタル処理が可能か
   * @return 成功したかどうか
   */
  bool client_stream_priority(int64_t stream_id,
                              uint32_t urgency,
                              bool incremental);

  /**
   * クライアント起動双方向ストリームの優先度を設定 (サーバー専用)
   *
   * クライアントから設定された優先度を上書きする。サーバーが
   * 設定すると、以降のクライアントからの優先度更新は無視される。
   * @param stream_id クライアント起動双方向ストリーム ID
   * @param urgency 優先度 (0-7。0 が最高)
   * @param incremental インクリメンタル処理が可能か
   * @return 成功したかどうか
   */
  bool server_stream_priority(int64_t stream_id,
                              uint32_t urgency,
                              bool incremental);

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
   * スケジューリングを再開する。
   * @param stream_id ストリーム ID
   * @return 成功したかどうか (存在しないストリームは成功扱い。メモリ
   *  不足の場合のみ false。コネクションが無いか閉じている場合は false)
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

 private:
  Http3Connection(bool is_server, const Http3Config& config);

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
  static int begin_trailers_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               void* conn_user_data,
                               void* stream_user_data);
  static int recv_trailer_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             int32_t token,
                             nghttp3_rcbuf* name,
                             nghttp3_rcbuf* value,
                             uint8_t flags,
                             void* conn_user_data,
                             void* stream_user_data);
  static int end_trailers_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             int fin,
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

  // ヘルパー
  void push_event(Http3Event event);

  // データ読み取りコールバック (nghttp3 用)
  static nghttp3_ssize read_data_cb(nghttp3_conn* conn,
                                    int64_t stream_id,
                                    nghttp3_vec* vec,
                                    size_t veccnt,
                                    uint32_t* pflags,
                                    void* conn_user_data,
                                    void* stream_user_data);

  bool is_server_;
  Http3Config config_;
  nghttp3_conn* conn_ = nullptr;

  // イベントキュー
  std::deque<Http3Event> events_;

  // ストリームデータ (送信待ち)
  std::map<int64_t, std::deque<StreamData>> stream_buffers_;

  // 送信待ちストリームデータ (nghttp3 から生成)
  std::deque<PendingStreamData> pending_sends_;

  // 現在受信中のヘッダー
  std::map<int64_t, std::vector<std::pair<std::string, std::string>>>
      pending_headers_;

  // 制御ストリーム ID
  int64_t control_stream_id_ = -1;
  int64_t qpack_encoder_stream_id_ = -1;
  int64_t qpack_decoder_stream_id_ = -1;

  // 接続状態
  bool closed_ = false;

  // shutdown_stream_write 済みのストリーム ID (send_data の no-op 用)
  std::set<int64_t> shutdown_stream_ids_;

  // goaway() 呼び出し済み (submit_shutdown_notice のガード用)
  bool shutdown_commenced_ = false;

  // submit_shutdown_notice 呼び出し済み (同一 GOAWAY ID の重複送信の防止用)
  bool shutdown_notice_sent_ = false;

  // set_max_client_streams_bidi で設定した累積最大数 (単調増加ガード用)
  uint64_t max_client_streams_bidi_ = 0;
};

// Python バインディングを定義
void bind_http3(nb::module_& m);

}  // namespace http3
}  // namespace webtransport
