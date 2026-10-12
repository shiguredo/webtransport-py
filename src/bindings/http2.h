/**
 * HTTP/2 バインディング (nghttp2-v2 ラッパー)
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

#include <nghttp2v2/nghttp2.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace http2 {

/**
 * HTTP/2 設定
 *
 * nghttp2-v2 は SETTINGS_MAX_FRAME_SIZE を送受信で 16384 固定として扱う
 * (nghttp2_conn.c の NGHTTP2_DEFAULT_MAX_FRAME_SIZE)。max_frame_size は
 * 16384 以外を設定しても使われない。
 */
struct Http2Config {
  // 初期ウィンドウサイズ (SETTINGS_INITIAL_WINDOW_SIZE)
  uint32_t initial_window_size = 65535;

  // 最大同時ストリーム数 (SETTINGS_MAX_CONCURRENT_STREAMS)
  uint32_t max_concurrent_streams = 100;

  // 最大フレームサイズ (nghttp2-v2 では 16384 固定。設定しても使われない)
  uint32_t max_frame_size = 16384;

  // 最大ヘッダーリストサイズ
  //
  // nghttp2-v2 はローカル側の最大ヘッダーリストサイズを強制しないため、
  // SETTINGS_MAX_HEADER_LIST_SIZE として広告もしない (local_settings は
  // 上限なしの 4294967295 を返す)
  uint32_t max_header_list_size = 65536;

  // サーバーモードかどうか
  bool is_server = false;

  // SETTINGS_NO_RFC7540_PRIORITIES を送信するか
  //
  // nghttp2-v2 は SETTINGS_NO_RFC7540_PRIORITIES=1 を常に送信するため、
  // このフィールドは互換のために残しているが動作には影響しない
  bool no_rfc7540_priorities = true;
};

/**
 * HTTP/2 イベント種別
 *
 * nghttp2-v2 では観測できないイベント (WINDOW_UPDATE / PUSH_PROMISE /
 * PRIORITY_UPDATE / 受信 PING) は削除している。PING は ACK の受信のみを
 * 観測できる (nghttp2_callbacks.recv_ping_ack)。
 */
enum class Http2EventType {
  Headers,
  Data,
  StreamEnd,
  StreamReset,
  GoAway,
  Settings,
  // PING ACK の受信 (受信 PING は nghttp2-v2 が自動で ACK を返すため
  // アプリへ通知されない)
  Ping,
  // 1xx (interim response)。RFC 9114 Section 4.1 / RFC 9113 Section 8.1
  Informational,
  // トレーラ (終端 HEADERS で :status を持たないもの)
  Trailers,
};

/**
 * HTTP/2 イベント
 */
struct Http2Event {
  Http2EventType type;
  int64_t stream_id = 0;
  std::vector<std::pair<std::string, std::string>> headers;
  std::vector<uint8_t> data;
  uint32_t error_code = 0;
  int64_t last_stream_id = 0;
  // PING ACK の 8 バイトの opaque data (RFC 9113 Section 6.7)。PING 以外では空
  std::vector<uint8_t> opaque_data;
  // PING ACK の RTT (ナノ秒)。nghttp2_callbacks.recv_ping_ack が計算した値。
  // PING ACK 以外では 0
  uint64_t ping_rtt_ns = 0;
};

/**
 * ストリームデータ (送信キュー用)
 */
struct StreamData {
  std::vector<uint8_t> data;
  // ライブラリへ渡して送出確認 (write_stream_data_offset) が済んだバイト数
  size_t offset = 0;
  bool eof = false;
};

/**
 * HTTP/2 コネクション (Sans-IO)
 *
 * TCP 上で動作する。TLS は Python 側で処理。
 *
 * nghttp2-v2 ではストリームのウィンドウ管理 (受信側) をアプリが行う。
 * このクラスは v1 相当の自動 WINDOW_UPDATE (しきい値は受信ウィンドウの
 * 半分) を実装しており、ウィンドウの状態も自前で追跡する。
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
   * @return 処理されたバイト数 (エラー時は 0)
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
  int64_t submit_request(
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * レスポンスを送信 (サーバー用)
   * @param stream_id ストリーム ID
   * @param headers レスポンスヘッダー
   */
  void submit_response(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * ストリームにデータを送信
   *
   * クライアントセッションでは submit_request、サーバーセッションでは
   * submit_response の後に呼ぶこと。トレーラを送信済みのストリームでは
   * トレーラ HEADERS が END_STREAM を担うため、eof=True の END_STREAM
   * 付与は無効化される (submit_trailer の説明を参照)
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param eof ストリーム終了フラグ
   */
  void send_data(int64_t stream_id,
                 const std::vector<uint8_t>& data,
                 bool eof = false);

  /**
   * ストリームを RST_STREAM でリセット
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void reset_stream(int64_t stream_id, uint32_t error_code = NGHTTP2_NO_ERROR);

  /**
   * GOAWAY を送信して graceful shutdown を開始する
   *
   * nghttp2-v2 の nghttp2_conn_shutdown は last_stream_id に処理済みの
   * 最大ストリーム ID を使い、エラーコードに NO_ERROR を固定する。
   * error_code に 0 以外を指定した場合は graceful shutdown で送れないため、
   * 即時終了 (terminate_session 相当) にフォールバックする
   * @param error_code エラーコード
   */
  void goaway(uint32_t error_code = NGHTTP2_NO_ERROR);

  /**
   * テスト専用: 低レベルを閉鎖状態にする (production からは呼ばない)
   *
   * nghttp2_conn_read / nghttp2_conn_write が負値を返した経路
   * (`closed_ = true`) を Python から人工的に作る。イベントは push しない
   */
  void test_force_close();

  /**
   * テスト専用: 送信バッファのエントリ数を返す (production からは呼ばない)
   */
  size_t test_stream_buffer_count(int64_t stream_id) const;

  /**
   * テスト専用: 送信バッファ先頭エントリの残バイト数を返す
   * (production からは呼ばない)
   */
  size_t test_stream_buffer_remaining(int64_t stream_id) const;

  /**
   * テスト専用: 送信バッファ先頭エントリの送出確認済みオフセットを返す
   * (production からは呼ばない)
   */
  size_t test_stream_buffer_offset(int64_t stream_id) const;

  /**
   * PING を送信
   *
   * nghttp2-v2 は同時に 1 つの PING のみ in-flight を許す。既に PING が
   * in-flight の場合は送出せず黙って無視する
   * @param opaque_data 8 バイトの opaque data (RFC 9113 Section 6.7)。
   *   空ならゼロ 8 バイトを送る。8 バイト以外は std::runtime_error
   */
  void ping(const std::vector<uint8_t>& opaque_data = {});

  /**
   * トレーラを送信 (サーバー用)
   *
   * nghttp2_conn_submit_trailers でトレーラ HEADERS を送出する。
   * トレーラ HEADERS が END_STREAM を担うため、以後の send_data は送出
   * されない。トレーラセクションは 1 つのみ (RFC 9113 8.1 節) のため、
   * 同一ストリームへの再呼び出しは失敗する
   * @param stream_id ストリーム ID
   * @param headers トレーラヘッダー
   * @return 成功したかどうか (クライアントセッション・コネクションが
   *  閉じている・ストリームが存在しない・レスポンス (データプロバイダ)
   *  が設定されていない・ローカル側が half-closed・eof=True のデータが
   *  積まれている・トレーラを送信済みの場合は false)
   */
  bool submit_trailer(
      int64_t stream_id,
      const std::vector<std::pair<std::string, std::string>>& headers);

  /**
   * RFC 9218 の PRIORITY_UPDATE フレームを送信 (クライアント用)
   *
   * nghttp2_conn_set_client_stream_priority で送信する。urgency と
   * incremental から Priority field value (u={urgency}、incremental のとき
   * は ", i") をシリアライズする。nghttp2-v2 はピアの
   * SETTINGS_NO_RFC7540_PRIORITIES を見ずに送出する
   * @param stream_id ストリーム ID
   * @param urgency 緊急度 (0-7)
   * @param incremental インクリメンタルかどうか
   * @return 成功したかどうか (サーバーセッション・コネクションが
   *  閉じている場合は false)
   */
  bool submit_priority_update(int64_t stream_id,
                              uint32_t urgency,
                              bool incremental);

  /**
   * ストリームの優先度を変更 (サーバー用)
   *
   * nghttp2_conn_set_server_stream_priority でローカルのスケジューリングに
   * 適用する。受信した PRIORITY_UPDATE は nghttp2-v2 が内部で適用するため
   * アプリから観測できない
   * @param stream_id ストリーム ID
   * @param urgency 緊急度 (0-7)
   * @param incremental インクリメンタルかどうか
   * @return 成功したかどうか (クライアントセッション・コネクションが
   *  閉じている場合は false)
   */
  bool change_extpri_stream_priority(int64_t stream_id,
                                     uint32_t urgency,
                                     bool incremental);

  /**
   * GOAWAY を送信してセッションを即時終了する
   *
   * nghttp2_conn_terminate で GOAWAY を送出し、以後の受信フレームを破棄
   * する。last_stream_id は nghttp2-v2 が処理済みの値を使うため指定でき
   * ない。パリティ検証のみ互換のために行う
   * @param error_code エラーコード
   * @param last_stream_id ピアが開始したストリーム ID (0 は処理済み
   *  ストリームなし = 全ストリームの終了。クライアントセッションでは
   *  偶数 / サーバーセッションでは奇数。パリティ違反と負の値は false)
   * @return 成功したかどうか (コネクションが閉じている場合は false)
   */
  bool terminate_session(uint32_t error_code, int64_t last_stream_id);

  /**
   * ローカルウィンドウサイズを動的に変更する
   *
   * stream_id 0 でコネクション全体、それ以外でストリーム単位。
   * window_size は絶対値 (delta ではない)。増加は WINDOW_UPDATE でピアへ
   * 通知されるが、減少はローカルでの受信絞り込みのみで通知されない。
   * 存在しないストリームと負の stream_id は成功扱い。
   * nghttp2-v2 では受信ウィンドウをアプリが管理するため、このメソッドは
   * 受信ウィンドウの追跡値を更新しつつ増加分を extend する
   * @param stream_id ストリーム ID (0 でコネクション全体)
   * @param window_size 設定するウィンドウサイズ (絶対値)
   * @return 成功したかどうか (負の window_size / コネクションが閉じている
   *  場合は false)
   */
  bool set_local_window_size(int64_t stream_id, int32_t window_size);

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
   * ピアから SETTINGS を受信する前は nghttp2-v2 のプロトコル既定値
   * (max_concurrent_streams のみセッション生成時に 100)。
   * max_frame_size は nghttp2-v2 が設定を扱わないため常に 16384
   * @return SETTINGS の辞書 (コネクションが閉じている場合は nullopt)
   */
  std::optional<std::map<std::string, uint32_t>> remote_settings() const;

  /**
   * ローカルの SETTINGS の値を取得
   *
   * ピアが ACK した値。 ACK を受信する前は nghttp2-v2 の既定値。
   * max_frame_size は 16384 固定、max_header_list_size は上限なしの
   * 4294967295 (nghttp2-v2 がローカルの上限を管理しないため)
   * @return SETTINGS の辞書 (コネクションが閉じている場合は nullopt)
   */
  std::optional<std::map<std::string, uint32_t>> local_settings() const;

  /**
   * コネクションのローカルウィンドウ残量を取得
   *
   * ピアが送れる量。 DATA の受信で減り、WINDOW_UPDATE の送出で増える
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
   * 枯渇後も False になる
   * @return 送信可否 (コネクションが閉じている場合は nullopt)
   */
  std::optional<bool> request_allowed() const;

  /**
   * ストリームのローカルウィンドウ残量を取得
   * @param stream_id ストリーム ID
   * @return ウィンドウ残量 (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<int32_t> stream_local_window_size(int64_t stream_id) const;

  /**
   * ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数を取得
   * @param stream_id ストリーム ID
   * @return 受信ウィンドウの消費量 (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<int32_t> stream_effective_recv_data_length(
      int64_t stream_id) const;

  /**
   * ストリームのローカル側が half-closed かを取得
   * @param stream_id ストリーム ID
   * @return half-closed かどうか (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<bool> stream_local_close(int64_t stream_id) const;

  /**
   * ストリームのリモート側が half-closed かを取得
   * @param stream_id ストリーム ID
   * @return half-closed かどうか (ストリームが存在しない・閉じている場合は nullopt)
   */
  std::optional<bool> stream_remote_close(int64_t stream_id) const;

 private:
  Http2Connection(bool is_server, const Http2Config& config);

  bool initialize();

  // データプロバイダ (nghttp2-v2 の read_data コールバック)
  static const nghttp2_data_reader* data_reader();

  // 送信データをバッファリングしつつ 1 チャンク生成する。
  // 既にバッファがあればそれを保持したまま true を返す
  bool ensure_pending_out();

  // ストリーム状態の追跡
  void register_stream(int64_t stream_id);
  void unregister_stream(int64_t stream_id);

  // nghttp2-v2 コールバック
  static int on_stream_open_callback(nghttp2_conn* conn,
                                     int64_t stream_id,
                                     void* user_data) noexcept;
  static int on_stream_close_callback(nghttp2_conn* conn,
                                      uint32_t flags,
                                      int64_t stream_id,
                                      uint32_t error_code,
                                      void* user_data,
                                      void* stream_user_data) noexcept;
  static int on_recv_settings_entry_callback(nghttp2_conn* conn,
                                             const nghttp2_settings_entry* ent,
                                             void* user_data) noexcept;
  static int on_recv_settings_callback(nghttp2_conn* conn,
                                       const nghttp2_proto_settings* settings,
                                       void* user_data) noexcept;
  static int on_recv_settings_ack_callback(nghttp2_conn* conn,
                                           void* user_data) noexcept;
  static int on_begin_headers_callback(nghttp2_conn* conn,
                                       int64_t stream_id,
                                       void* user_data,
                                       void* stream_user_data) noexcept;
  static int on_recv_header_callback(nghttp2_conn* conn,
                                     int64_t stream_id,
                                     int32_t token,
                                     nghttp2_rcbuf* name,
                                     nghttp2_rcbuf* value,
                                     uint8_t flags,
                                     void* user_data,
                                     void* stream_user_data) noexcept;
  static int on_end_headers_callback(nghttp2_conn* conn,
                                     int64_t stream_id,
                                     int fin,
                                     void* user_data,
                                     void* stream_user_data) noexcept;
  static int on_begin_trailers_callback(nghttp2_conn* conn,
                                        int64_t stream_id,
                                        void* user_data,
                                        void* stream_user_data) noexcept;
  static int on_recv_trailer_callback(nghttp2_conn* conn,
                                      int64_t stream_id,
                                      int32_t token,
                                      nghttp2_rcbuf* name,
                                      nghttp2_rcbuf* value,
                                      uint8_t flags,
                                      void* user_data,
                                      void* stream_user_data) noexcept;
  static int on_end_trailers_callback(nghttp2_conn* conn,
                                      int64_t stream_id,
                                      int fin,
                                      void* user_data,
                                      void* stream_user_data) noexcept;
  static int on_recv_data_callback(nghttp2_conn* conn,
                                   int64_t stream_id,
                                   const uint8_t* data,
                                   size_t datalen,
                                   void* user_data,
                                   void* stream_user_data) noexcept;
  static int on_remote_end_stream_callback(nghttp2_conn* conn,
                                           int64_t stream_id,
                                           void* user_data,
                                           void* stream_user_data) noexcept;
  static int on_local_end_stream_callback(nghttp2_conn* conn,
                                          int64_t stream_id,
                                          void* user_data,
                                          void* stream_user_data) noexcept;
  static int on_recv_ping_ack_callback(nghttp2_conn* conn,
                                       const nghttp2_ping_data* data,
                                       nghttp2_duration rtt,
                                       void* user_data) noexcept;
  static int on_shutdown_callback(nghttp2_conn* conn,
                                  int64_t last_stream_id,
                                  uint32_t error_code,
                                  void* user_data) noexcept;
  static nghttp2_ssize on_read_data_callback(nghttp2_conn* conn,
                                             int64_t stream_id,
                                             nghttp2_vec* vec,
                                             size_t veccnt,
                                             uint32_t* pflags,
                                             void* user_data,
                                             void* stream_user_data) noexcept;
  static int on_write_stream_data_offset_callback(
      nghttp2_conn* conn,
      int64_t stream_id,
      uint64_t offset,
      size_t len,
      void* user_data,
      void* stream_user_data) noexcept;
  static void on_rand_callback(uint8_t* dest, size_t destlen) noexcept;

  // ヘルパー
  void push_event(Http2Event event);

  // 受信 DATA のフロー制御 (v1 相当の自動 WINDOW_UPDATE)
  void extend_recv_connection_window(size_t size);
  void extend_recv_stream_window(int64_t stream_id, size_t size);

  bool is_server_;
  Http2Config config_;
  nghttp2_conn* conn_ = nullptr;

  // イベントキュー
  std::deque<Http2Event> events_;

  // 送信バッファ (nghttp2_conn_write が生成した未送信バイト列)
  std::vector<uint8_t> pending_out_;

  // ストリームデータ (送信待ち)
  std::map<int64_t, std::deque<StreamData>> stream_buffers_;

  // ライブラリが送出確認した絶対オフセット (write_stream_data_offset)
  std::map<int64_t, uint64_t> stream_data_sent_;

  // トレーラを送信済みのストリーム
  std::set<int64_t> trailers_submitted_;

  // ローカルから RST_STREAM を送ったストリーム (StreamReset イベントの判定用)
  std::set<int64_t> local_resets_;

  // ストリーム状態 (ローカル側 / リモート側の half-closed 追跡)
  struct StreamRuntime {
    bool local_closed = false;
    bool remote_closed = false;
  };
  std::map<int64_t, StreamRuntime> streams_;

  // 受信中のヘッダー / トレーラ
  std::map<int64_t, std::vector<std::pair<std::string, std::string>>>
      pending_headers_;
  std::map<int64_t, std::vector<std::pair<std::string, std::string>>>
      pending_trailers_;

  // コネクションレベルの受信ウィンドウ追跡 (v1 の recv_window_size 相当)
  int64_t conn_local_window_size_ = NGHTTP2_INITIAL_WINDOW_SIZE;
  int64_t conn_recv_window_size_ = 0;
  int64_t conn_recv_reduction_ = 0;

  // ストリームごとの受信ウィンドウ追跡
  std::map<int64_t, int64_t> stream_local_window_size_;
  std::map<int64_t, int64_t> stream_recv_window_size_;
  std::map<int64_t, int64_t> stream_recv_reduction_;

  // 接続状態
  bool closed_ = false;
  // GOAWAY を送信済み / graceful shutdown が完了した (nghttp2_conn_write が
  // NGHTTP2_ERR_CLOSING を返した) かどうか
  bool closing_ = false;
  bool goaway_sent_ = false;
  // ピアから GOAWAY を受信したか
  bool goaway_received_ = false;
};

// Python バインディングを定義
void bind_http2(nb::module_& m);

}  // namespace http2
}  // namespace webtransport
