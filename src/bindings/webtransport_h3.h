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
   * @param stream_id セッションストリーム ID
   * @return 成功したかどうか
   */
  bool accept_session(int64_t stream_id);

  /**
   * WebTransport セッションを拒否 (サーバー用)
   * @param stream_id セッションストリーム ID
   * @param status_code HTTP ステータスコード
   */
  void reject_session(int64_t stream_id, int status_code);

  /**
   * WebTransport ストリームを開く
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
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_stream_data(int64_t stream_id,
                        const std::vector<uint8_t>& data,
                        bool fin = false);

  /**
   * WebTransport データグラムを送信
   * @param session_id セッション ID
   * @param data データグラムペイロード
   */
  void send_datagram(int64_t session_id, const std::vector<uint8_t>& data);

  /**
   * WebTransport ストリームを閉じる (nghttp3 にクローズを通知)
   *
   * QUIC 側で RESET_STREAM を送った後、または対向から RESET_STREAM を
   * 受信した後に呼び出す。
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void close_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * WebTransport ストリームをリセットする
   *
   * close_stream と同じ。高レベル API では QUIC RESET_STREAM 送出と合わせて使う。
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

  // ストリームとセッションのマッピング
  std::map<int64_t, StreamInfo> stream_info_;

  // 制御ストリーム ID
  int64_t control_stream_id_ = -1;
  int64_t qpack_encoder_stream_id_ = -1;
  int64_t qpack_decoder_stream_id_ = -1;

  // 接続状態
  bool closed_ = false;
};

// Python バインディングを定義
void bind_webtransport_h3(nb::module_& m);

}  // namespace h3
}  // namespace webtransport
