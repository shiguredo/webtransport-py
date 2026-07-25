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
  PushPromise,
  GoAway,
  // 後方互換: Reset は ResetStream と同義
  Reset,
  // nghttp3 が QUIC 層への送出を要求する
  ResetStream,
  StopSending,
  // WebTransport 用
  WebTransportSessionReady,
  WebTransportStreamData,
  WebTransportDatagram,
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
  void reset_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * QUIC ストリーム終了を nghttp3 に通知する
   *
   * QUIC の STREAM_CLOSED を受けたときに呼ぶ。
   * nghttp3 の stream_close コールバック経由で STREAM_END イベントが生成される。
   *
   * @param stream_id ストリーム ID
   * @param error_code アプリケーションエラーコード
   */
  void close_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * GOAWAY を送信
   * @param id GOAWAY ID
   */
  void goaway(int64_t id = 0);

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
};

// Python バインディングを定義
void bind_http3(nb::module_& m);

}  // namespace http3
}  // namespace webtransport
