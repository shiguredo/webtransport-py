/**
 * HTTP/2 バインディング (nghttp2 ラッパー)
 *
 * Sans-IO スタイルの HTTP/2 実装
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/map.h>
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

  // SETTINGS_NO_RFC7540_PRIORITIES を送信するか
  // (RFC 9218 の拡張優先度を有効にする。true にすると SETTINGS に
  // NO_RFC7540_PRIORITIES=1 が含まれる)
  bool no_rfc7540_priorities = true;
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
  PushPromise,
  PriorityUpdate,
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
  int32_t promised_stream_id = 0;
  // PRIORITY_UPDATE の priority field value (例: "u=5, i")
  std::string priority_field_value;
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
   *
   * データプロバイダを常に登録するため、 HEADERS に END_STREAM は付かない。
   * リクエストの終端は send_data(stream_id, data, eof=True) で行う
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
   *
   * クライアントセッションでは submit_request、サーバーセッションでは
   * submit_response の後に呼ぶこと。トレーラを予約済みのストリームでは
   * トレーラ HEADERS が END_STREAM を担うため、eof=True の END_STREAM
   * 付与は無効化される (予約が無ければ従来どおり eof=True で終端する)
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
   * トレーラを送信 (サーバー用)
   *
   * トレーラ HEADERS は END_STREAM を担う。send_data(stream_id, data,
   * eof=False) でデータを積んだ後に呼び、send() で flush する。
   * データの最終チャンクが送出される時点で nghttp2_submit_trailer が
   * 呼ばれ、DATA の後にトレーラ HEADERS が送信される。データが既に
   * flush された後でも、deferred 状態を再開して送信する。トレーラを
   * 送らない場合は従来どおり eof=True で終端する。トレーラセクションは
   * 1 つのみ (RFC 9113 8.1 節) のため、同一ストリームへの再呼び出しは
   * 失敗する
   * @param stream_id ストリーム ID
   * @param headers トレーラヘッダー
   * @return 成功したかどうか (クライアントセッション・コネクションが
   *  閉じている・ストリームが存在しない・レスポンス (データプロバイダ)
   *  が設定されていない・ローカル側が half-closed・eof=True のデータが
   *  積まれている・トレーラを予約済みの場合は false)
   */
  bool submit_trailer(
      int32_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * RFC 9218 の PRIORITY_UPDATE フレームを送信 (クライアント用)
   *
   * urgency と incremental から Priority field value
   * (u={urgency}、incremental のとき , i) をシリアライズして送信する。
   * 動作にはピアが SETTINGS_NO_RFC7540_PRIORITIES=1 を送信している
   * 必要がある (ピアが SETTINGS で NO_RFC7540_PRIORITIES=0 を送信した
   * 場合のみ nghttp2 が noop で成功を返す)
   * @param stream_id ストリーム ID
   * @param urgency 緊急度 (0-7)
   * @param incremental インクリメンタルかどうか
   * @return 成功したかどうか (サーバーセッション・コネクションが
   *  閉じている場合は false)
   */
  bool submit_priority_update(int32_t stream_id,
                              uint32_t urgency,
                              bool incremental);

  /**
   * ストリームの優先度を変更 (サーバー用)
   *
   * RFC 9218 の拡張優先度をローカルのスケジューリングに適用する。
   * ignore_client_signal は常に 1 (クライアントからの優先度更新を無視)。
   * 動作には自己が SETTINGS_NO_RFC7540_PRIORITIES=1 を送信している
   * 必要がある (未送信時は nghttp2 が noop で成功を返す)
   * @param stream_id ストリーム ID
   * @param urgency 緊急度 (0-7)
   * @param incremental インクリメンタルかどうか
   * @return 成功したかどうか (クライアントセッション・コネクションが
   *  閉じている場合は false)
   */
  bool change_extpri_stream_priority(int32_t stream_id,
                                     uint32_t urgency,
                                     bool incremental);

  /**
   * Server Push を宣言 (サーバー用)
   * @param stream_id 親ストリーム ID
   * @param headers プッシュするリクエストヘッダー
   * @return promised stream ID (失敗時は -1)
   */
  int32_t submit_push_promise(
      int32_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * GOAWAY を送信してセッションを即時終了する
   *
   * 既存の goaway() と異なり graceful shutdown ではなく、呼び出し直後から
   * 受信フレームを無視し、GOAWAY 送出後に want_read / want_write が 0 に
   * なって終了する。closed_ にはしないため is_closed() は False のまま。
   * 2 回目の呼び出しは何もせず成功を返す。goaway() の後に呼ぶと GOAWAY が
   * 2 枚送信される (RFC 9113 6.8 では許容される。2 枚目の last_stream_id
   * は既に送信した値より大きくならない)
   * @param error_code エラーコード
   * @param last_stream_id ピアが開始したストリーム ID (0 は処理済み
   *  ストリームなし = 全ストリームの終了。クライアントセッションでは
   *  偶数 / サーバーセッションでは奇数。パリティ違反と負の値は false)
   * @return 成功したかどうか (コネクションが閉じている場合は false)
   */
  bool terminate_session(uint32_t error_code, int32_t last_stream_id);

  /**
   * ローカルウィンドウサイズを動的に変更する
   *
   * stream_id 0 でコネクション全体、それ以外でストリーム単位。
   * window_size は絶対値 (delta ではない)。増加は WINDOW_UPDATE でピアへ
   * 通知されるが、減少はローカルでの受信絞り込みのみで通知されない。
   * 存在しないストリームと負の stream_id は成功扱いになる (nghttp2
   * v1.70.0 の実装。ヘッダー doc の INVALID_ARGUMENT とは異なる)
   * @param stream_id ストリーム ID (0 でコネクション全体)
   * @param window_size 設定するウィンドウサイズ (絶対値)
   * @return 成功したかどうか (負の window_size / コネクションが閉じている
   *  場合は false)
   */
  bool set_local_window_size(int32_t stream_id, int32_t window_size);

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

  /**
   * ピアの SETTINGS の値を取得
   *
   * ピアから SETTINGS を受信する前は nghttp2 のデフォルト値
   * (max_concurrent_streams のみセッション生成時に 100)
   * @return SETTINGS の辞書 (コネクションが閉じている場合は nullopt)
   */
  std::optional<std::map<std::string, uint32_t>> remote_settings() const;

  /**
   * ローカルの SETTINGS の値を取得
   *
   * ピアが ACK した値。 ACK を受信する前は nghttp2 のデフォルト値
   * @return SETTINGS の辞書 (コネクションが閉じている場合は nullopt)
   */
  std::optional<std::map<std::string, uint32_t>> local_settings() const;

  /**
   * 送信キューのフレーム数を取得 (deferred DATA を含まない)
   * @return フレーム数 (コネクションが閉じている場合は nullopt)
   */
  std::optional<size_t> outbound_queue_size() const;

  /**
   * コネクションのリモートウィンドウ残量を取得
   *
   * ローカルが送れる量。 DATA の送出で減り、ピアの WINDOW_UPDATE 受信で
   * 増える。コネクションウィンドウは SETTINGS_INITIAL_WINDOW_SIZE の
   * 影響を受けない
   * @return ウィンドウ残量 (コネクションが閉じている場合は nullopt)
   */
  std::optional<int32_t> remote_window_size() const;

  /**
   * コネクションのローカルウィンドウ残量を取得
   *
   * ピアが送れる量。 DATA の受信で減り、WINDOW_UPDATE の送出で増える。
   * コネクションウィンドウは SETTINGS_INITIAL_WINDOW_SIZE の影響を受けない
   * @return ウィンドウ残量 (コネクションが閉じている場合は nullopt)
   */
  std::optional<int32_t> local_window_size() const;

  /**
   * WINDOW_UPDATE を送信せずに受信した DATA ペイロードのバイト数を取得
   * @return 受信ウィンドウの消費量 (コネクションが閉じている場合は nullopt)
   */
  std::optional<int32_t> effective_recv_data_length() const;

  /**
   * 新しいリクエストを送信できるかを取得 (クライアントのみ)
   *
   * サーバーセッションでは常に False。 GOAWAY の受信後とストリーム ID の
   * 枯渇後も False になる。 GOAWAY 送信後は、アクティブストリームが無く
   * 送信待ちが無くなった場合に False になる
   * @return 送信可否 (コネクションが閉じている場合は nullopt)
   */
  std::optional<bool> request_allowed() const;

  /**
   * ストリームのリモートウィンドウ残量を取得
   *
   * SETTINGS_INITIAL_WINDOW_SIZE の縮小で内部のウィンドウが負になりうる
   * が、 nghttp2 が 0 にクランプして返す
   * @param stream_id ストリーム ID
   * @return ウィンドウ残量 (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<int32_t> stream_remote_window_size(int32_t stream_id) const;

  /**
   * ストリームのローカルウィンドウ残量を取得
   *
   * SETTINGS_INITIAL_WINDOW_SIZE の縮小で内部のウィンドウが負になりうる
   * が、 nghttp2 が 0 にクランプして返す
   * @param stream_id ストリーム ID
   * @return ウィンドウ残量 (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<int32_t> stream_local_window_size(int32_t stream_id) const;

  /**
   * ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数を取得
   * @param stream_id ストリーム ID
   * @return 受信ウィンドウの消費量 (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<int32_t> stream_effective_recv_data_length(
      int32_t stream_id) const;

  /**
   * ストリームのローカル側が half-closed かを取得
   * @param stream_id ストリーム ID
   * @return half-closed かどうか (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<bool> stream_local_close(int32_t stream_id) const;

  /**
   * ストリームのリモート側が half-closed かを取得
   * @param stream_id ストリーム ID
   * @return half-closed かどうか (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<bool> stream_remote_close(int32_t stream_id) const;

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

  // 保留中のトレーラ (送信待ち)
  std::map<int32_t, std::vector<std::pair<std::string, std::string>>>
      pending_trailers_;

  // 接続状態
  bool closed_ = false;
  bool goaway_sent_ = false;
  // ピアから GOAWAY を受信したか (RFC 9113 Section 6.8 の graceful
  // shutdown。受信後も既存ストリームの送受信は継続し、新規ストリームの
  // 開始のみを抑止する。closed_ にはしない。なお WebTransport over HTTP/2
  // (draft-15 Section 6.13 の「ピアは GOAWAY 後に新規 WebTransport ストリーム
  // を開いても MAY」) との相互作用は別途検討する)
  bool goaway_received_ = false;
};

// Python バインディングを定義
void bind_http2(nb::module_& m);

}  // namespace http2
}  // namespace webtransport
