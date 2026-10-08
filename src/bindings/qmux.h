/**
 * QMux (dwnx) バインディング
 *
 * QMux は TLS/TCP のような双方向バイトストリーム上で、QUIC v1 相当のストリームと
 * 多重化を提供するプロトコル (draft-ietf-quic-qmux)。ngtcp2 と同型の API を持つ
 * dwnx を Sans-IO で包む。バイトストリーム型のため、QUIC のような UDP パケットの
 * 概念は持たず、1 レコード分のバイト列を入出力する。
 */

#ifndef WEBTRANSPORT_BINDINGS_QMUX_H
#define WEBTRANSPORT_BINDINGS_QMUX_H

#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <dwnx/dwnx.h>
#include <nanobind/nanobind.h>

namespace webtransport::qmux {

/**
 * QMux 接続の設定 (dwnx の settings と transport parameters)
 */
struct QmuxConfig {
  // ローカルが受け入れる同時ストリーム数
  uint64_t initial_max_streams_bidi = 100;
  uint64_t initial_max_streams_uni = 100;
  // コネクション全体のフロー制御上限 (バイト)
  uint64_t initial_max_data = 1 << 20;
  // ストリームごとのフロー制御上限 (バイト)
  uint64_t initial_max_stream_data_bidi_local = 1 << 18;
  uint64_t initial_max_stream_data_bidi_remote = 1 << 18;
  uint64_t initial_max_stream_data_uni = 1 << 18;
  // アイドルタイムアウト (ナノ秒)
  uint64_t max_idle_timeout_ns = 30'000'000'000ULL;
  // 1 レコードの最大長 (バイト)
  uint64_t max_record_size = 16384;
};

/**
 * QMux イベント種別
 */
enum class QmuxEventType {
  TransportParamsReceived,
  StreamData,
  StreamClosed,
  StreamReset,
  StopSending,
};

/**
 * QMux イベント
 */
struct QmuxEvent {
  QmuxEventType type;
  int64_t stream_id = -1;
  uint64_t offset = 0;
  std::vector<uint8_t> data;
  bool fin = false;
  uint64_t error_code = 0;
  // TransportParamsReceived のときだけ入る (エンコード済みのトランスポートパラメータ)
  std::vector<uint8_t> transport_params;
};

/**
 * QMux 接続 (Sans-IO)
 *
 * 受信したバイト列を `receive()` へ渡し、送信すべきレコードを `send()` で取り出す。
 * 部分的なレコードの保持は dwnx が行うため、呼び出し側はバイトストリームから
 * 読めた分をそのまま渡せる。
 */
class QmuxConnection {
 public:
  /**
   * クライアント接続を作成する
   */
  static std::unique_ptr<QmuxConnection> create_client(
      const QmuxConfig& config);

  /**
   * サーバー接続を作成する
   */
  static std::unique_ptr<QmuxConnection> create_server(
      const QmuxConfig& config);

  ~QmuxConnection();

  QmuxConnection(const QmuxConnection&) = delete;
  QmuxConnection& operator=(const QmuxConnection&) = delete;

  /**
   * 受信したバイト列を処理する (dwnx_conn_read)
   *
   * @return 0 以上は成功、負値は dwnx のライブラリエラー
   */
  int receive(const uint8_t* data, size_t datalen);

  /**
   * 送信すべき 1 レコードを取り出す
   *
   * 送るものがある場合は 1 レコード分のバイト列、無い場合は nullopt。
   */
  std::optional<std::vector<uint8_t>> send();

  /**
   * 次のタイマー期限を取得する (ナノ秒。無い場合は nullopt)
   */
  std::optional<uint64_t> get_timeout() const;

  /**
   * タイマーを処理する
   */
  void handle_timeout();

  /**
   * ストリームを開く
   *
   * @return ストリーム ID。失敗時は -1
   */
  int64_t open_stream(bool bidirectional);

  /**
   * ストリームデータの送信を予約する (実際の送出は send())
   */
  void send_stream_data(int64_t stream_id,
                        const uint8_t* data,
                        size_t datalen,
                        bool fin);

  /**
   * 接続を閉じる (CONNECTION_CLOSE を含むレコードを send() で取り出せるようにする)
   */
  void close(uint64_t error_code, const std::string& reason);

  /**
   * 次のイベントを取り出す
   */
  std::optional<QmuxEvent> next_event();

  bool is_server() const;

  uint64_t streams_bidi_left() const;
  uint64_t streams_uni_left() const;

  /**
   * dwnx のライブラリエラーコードを文字列にする
   */
  static std::string strerror(int liberr);

 private:
  QmuxConnection() = default;

  /**
   * dwnx の接続を作成する
   */
  void initialize(const QmuxConfig& config, bool is_server);

  // 送信待ちのストリームデータ (ngtcp2 と同じく、送信時にデータを渡す契約のため)
  struct PendingStreamData {
    int64_t stream_id;
    std::vector<uint8_t> data;
    bool fin;
  };

  std::optional<std::vector<uint8_t>> write_pending_stream_data();
  /**
   * エンコード済みのストリームデータを送信待ちから取り除く
   */
  void consume_stream_data(dwnx_ssize datalen);
  std::optional<std::vector<uint8_t>> write_connection_close();
  void push_event(QmuxEvent event);

  dwnx_conn* conn_ = nullptr;
  std::deque<QmuxEvent> events_;
  std::deque<PendingStreamData> pending_stream_data_;
  // close() で作った CONNECTION_CLOSE のレコード
  std::optional<std::vector<uint8_t>> pending_close_record_;
  std::vector<uint8_t> write_buffer_;
  // close() で作った CONNECTION_CLOSE の内容 (send() で 1 回だけ取り出す)
  dwnx_ccerr close_ccerr_{};
  bool close_requested_ = false;

  friend int recv_transport_params_cb(dwnx_conn* conn,
                                      const dwnx_transport_params* params,
                                      void* user_data) noexcept;
  friend int recv_stream_data_cb(dwnx_conn* conn,
                                 uint32_t flags,
                                 int64_t stream_id,
                                 uint64_t offset,
                                 const uint8_t* data,
                                 size_t datalen,
                                 void* user_data,
                                 void* stream_user_data) noexcept;
  friend int stream_close_cb(dwnx_conn* conn,
                             uint32_t flags,
                             int64_t stream_id,
                             uint64_t rx_app_error_code,
                             uint64_t tx_app_error_code,
                             void* user_data,
                             void* stream_user_data) noexcept;
  friend int stream_reset_cb(dwnx_conn* conn,
                             int64_t stream_id,
                             uint64_t final_size,
                             uint64_t app_error_code,
                             void* user_data,
                             void* stream_user_data) noexcept;
  friend int recv_stop_sending_cb(dwnx_conn* conn,
                                  int64_t stream_id,
                                  uint64_t app_error_code,
                                  void* user_data,
                                  void* stream_user_data) noexcept;
};

}  // namespace webtransport::qmux

/**
 * webtransport_ext.qmux サブモジュールを登録する
 */
void bind_qmux(nanobind::module_& m);

#endif  // WEBTRANSPORT_BINDINGS_QMUX_H
