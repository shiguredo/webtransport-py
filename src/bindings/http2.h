/**
 * HTTP/2 バインディング (nghttp2 ラッパー)
 *
 * Sans-IO スタイルの HTTP/2 実装
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

// Windows では ssize_t が定義されていないため定義する
#ifdef _WIN32
#include <BaseTsd.h>
typedef SSIZE_T ssize_t;
#endif

#include <nghttp2/nghttp2.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace http2 {

/**
 * HTTP/2 設定
 */
struct Http2Config {
  // 初期ウィンドウサイズ
  uint32_t initial_window_size = 65535;

  // 最大同時ストリーム数
  uint32_t max_concurrent_streams = 100;

  // 最大フレームサイズ
  uint32_t max_frame_size = 16384;

  // 最大ヘッダーリストサイズ
  uint32_t max_header_list_size = 65536;

  // サーバーモードかどうか
  bool is_server = false;

  // HTTP/2 プリフェイスを送信するか
  bool send_preface = true;
};

/**
 * HTTP/2 イベント種別
 */
enum class Http2EventType {
  Headers,
  Data,
  StreamEnd,
  StreamReset,
  GoAway,
  WindowUpdate,
  Settings,
  Ping,
};

/**
 * HTTP/2 イベント
 */
struct Http2Event {
  Http2EventType type;
  int32_t stream_id = 0;
  std::vector<std::pair<std::string, std::string>> headers;
  std::vector<uint8_t> data;
  uint32_t error_code = 0;
  int32_t last_stream_id = 0;
};

/**
 * ストリームデータ (送信キュー用)
 */
struct StreamData {
  std::vector<uint8_t> data;
  bool eof = false;
};

/**
 * HTTP/2 コネクション (Sans-IO)
 *
 * TCP 上で動作する。TLS は Python 側で処理。
 */
class Http2Connection {
 public:
  /**
   * クライアントとして接続を作成
   */
  static std::unique_ptr<Http2Connection> create_client(
      const Http2Config& config);

  /**
   * サーバーとして接続を作成
   */
  static std::unique_ptr<Http2Connection> create_server(
      const Http2Config& config);

  ~Http2Connection();

  // コピー禁止
  Http2Connection(const Http2Connection&) = delete;
  Http2Connection& operator=(const Http2Connection&) = delete;

  // ムーブ許可
  Http2Connection(Http2Connection&&) noexcept;
  Http2Connection& operator=(Http2Connection&&) noexcept;

  /**
   * 受信したデータを処理
   * @param data 受信したデータ
   * @return 処理されたバイト数
   */
  size_t receive(const std::vector<uint8_t>& data);

  /**
   * 送信すべきデータを取得
   * @return 送信すべきデータ (なければ空)
   */
  std::optional<std::vector<uint8_t>> send();

  /**
   * リクエストを送信 (クライアント用)
   * @param headers リクエストヘッダー
   * @return ストリーム ID (失敗時は -1)
   */
  int32_t submit_request(
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * レスポンスを送信 (サーバー用)
   * @param stream_id ストリーム ID
   * @param headers レスポンスヘッダー
   */
  void submit_response(
      int32_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * ストリームにデータを送信
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param eof ストリーム終了フラグ
   */
  void send_data(int32_t stream_id,
                 const std::vector<uint8_t>& data,
                 bool eof = false);

  /**
   * ストリームを RST_STREAM でリセット
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void reset_stream(int32_t stream_id, uint32_t error_code = NGHTTP2_NO_ERROR);

  /**
   * GOAWAY を送信して接続を閉じる
   * @param error_code エラーコード
   */
  void goaway(uint32_t error_code = NGHTTP2_NO_ERROR);

  /**
   * PING を送信
   */
  void ping();

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<Http2Event> next_event();

  /**
   * セッションが送信待ちデータを持っているか
   */
  bool want_write() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

 private:
  Http2Connection(bool is_server, const Http2Config& config);

  bool initialize();

  // nghttp2 コールバック
  static ssize_t send_callback(nghttp2_session* session,
                               const uint8_t* data,
                               size_t length,
                               int flags,
                               void* user_data);
  static int on_frame_recv_callback(nghttp2_session* session,
                                    const nghttp2_frame* frame,
                                    void* user_data);
  static int on_data_chunk_recv_callback(nghttp2_session* session,
                                         uint8_t flags,
                                         int32_t stream_id,
                                         const uint8_t* data,
                                         size_t len,
                                         void* user_data);
  static int on_stream_close_callback(nghttp2_session* session,
                                      int32_t stream_id,
                                      uint32_t error_code,
                                      void* user_data);
  static int on_header_callback(nghttp2_session* session,
                                const nghttp2_frame* frame,
                                const uint8_t* name,
                                size_t namelen,
                                const uint8_t* value,
                                size_t valuelen,
                                uint8_t flags,
                                void* user_data);
  static int on_begin_headers_callback(nghttp2_session* session,
                                       const nghttp2_frame* frame,
                                       void* user_data);
  static ssize_t data_source_read_callback(nghttp2_session* session,
                                           int32_t stream_id,
                                           uint8_t* buf,
                                           size_t length,
                                           uint32_t* data_flags,
                                           nghttp2_data_source* source,
                                           void* user_data);

  // ヘルパー
  void push_event(Http2Event event);

  bool is_server_;
  Http2Config config_;
  nghttp2_session* session_ = nullptr;

  // イベントキュー
  std::deque<Http2Event> events_;

  // 送信バッファ
  std::vector<uint8_t> send_buffer_;

  // ストリームデータ (送信待ち)
  std::map<int32_t, std::deque<StreamData>> stream_buffers_;

  // 現在受信中のヘッダー
  std::map<int32_t, std::vector<std::pair<std::string, std::string>>>
      pending_headers_;

  // 接続状態
  bool closed_ = false;
  bool goaway_sent_ = false;
};

// Python バインディングを定義
void bind_http2(nb::module_& m);

}  // namespace http2
}  // namespace webtransport
