/**
 * QUIC バインディング (ngtcp2 ラッパー)
 *
 * Sans-IO スタイルの QUIC 実装
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_boringssl.h>

#include <openssl/ssl.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace quic {

/**
 * QUIC コネクション設定
 */
struct QuicConfig {
  // 最大ストリーム数
  uint64_t max_streams_bidi = 100;
  uint64_t max_streams_uni = 100;

  // 最大データサイズ
  uint64_t max_data = 1048576;
  uint64_t max_stream_data_bidi_local = 262144;
  uint64_t max_stream_data_bidi_remote = 262144;
  uint64_t max_stream_data_uni = 262144;

  // アイドルタイムアウト (ナノ秒)
  uint64_t idle_timeout_ns = 30000000000ULL;  // 30秒

  // ALPN (Application-Layer Protocol Negotiation)
  std::vector<std::string> alpn;

  // サーバー名 (SNI)
  std::string server_name;

  // 証明書関連 (サーバー用)
  std::string cert_file;
  std::string key_file;

  // クライアント認証を要求するか
  bool verify_peer = false;

  // Datagram サポート
  bool enable_datagram = true;
  uint64_t max_datagram_frame_size = 65536;
};

/**
 * QUIC イベント種別
 */
enum class QuicEventType {
  HandshakeCompleted,
  ConnectionClosed,
  StreamData,
  StreamOpened,
  StreamClosed,
  StreamReset,
  DatagramReceived,
  ConnectionIdRetired,
};

/**
 * QUIC イベント
 */
struct QuicEvent {
  QuicEventType type;
  int64_t stream_id = -1;
  std::vector<uint8_t> data;
  bool fin = false;
  uint64_t error_code = 0;
  std::string reason;
};

/**
 * ストリームデータ (送信キュー用)
 */
struct StreamData {
  std::vector<uint8_t> data;
  bool fin = false;
};

/**
 * QUIC コネクション (Sans-IO)
 *
 * I/O は Python 側で処理し、このクラスはプロトコル処理のみを行う。
 */
class QuicConnection {
 public:
  /**
   * クライアントとして接続を作成
   */
  static std::unique_ptr<QuicConnection> create_client(
      const QuicConfig& config);

  /**
   * サーバーとして接続を作成 (直接作成用)
   */
  static std::unique_ptr<QuicConnection> create_server(
      const QuicConfig& config);

  /**
   * 初期パケットからサーバー接続を作成
   * @param config 設定
   * @param initial_packet クライアントからの初期パケット
   * @return 接続 (失敗時は nullptr)
   */
  static std::unique_ptr<QuicConnection> accept(
      const QuicConfig& config,
      const std::vector<uint8_t>& initial_packet);

  ~QuicConnection();

  // コピー禁止
  QuicConnection(const QuicConnection&) = delete;
  QuicConnection& operator=(const QuicConnection&) = delete;

  // ムーブ許可
  QuicConnection(QuicConnection&&) noexcept;
  QuicConnection& operator=(QuicConnection&&) noexcept;

  /**
   * 受信したデータを処理
   * @param data 受信した UDP パケット
   * @return 処理されたバイト数
   */
  size_t receive(const std::vector<uint8_t>& data);

  /**
   * 送信すべきデータを取得
   * @return 送信すべき UDP パケット (なければ空)
   */
  std::optional<std::vector<uint8_t>> send();

  /**
   * 次のタイムアウトまでの時間を取得
   * @return タイムアウトまでのナノ秒 (タイムアウトがなければ nullopt)
   */
  std::optional<uint64_t> get_timeout_ns() const;

  /**
   * タイムアウトを処理
   */
  void handle_timeout();

  /**
   * ストリームを開く
   * @param bidirectional 双方向ストリームかどうか
   * @return ストリーム ID (失敗時は -1)
   */
  int64_t open_stream(bool bidirectional = true);

  /**
   * ストリームにデータを送信
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_stream_data(int64_t stream_id,
                        const std::vector<uint8_t>& data,
                        bool fin = false);

  /**
   * ストリームを閉じる
   * @param stream_id ストリーム ID
   * @param error_code エラーコード (0 なら正常終了)
   */
  void close_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * Datagram を送信
   * @param data Datagram データ
   */
  void send_datagram(const std::vector<uint8_t>& data);

  /**
   * 接続を閉じる
   * @param error_code エラーコード
   * @param reason 理由
   */
  void close(uint64_t error_code = 0, const std::string& reason = "");

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<QuicEvent> next_event();

  /**
   * 接続が確立されているか
   */
  bool is_established() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

  /**
   * ハンドシェイクが完了したか
   */
  bool is_handshake_completed() const;

  /**
   * 接続 ID を取得
   */
  std::vector<uint8_t> get_connection_id() const;

 private:
  QuicConnection(bool is_server, const QuicConfig& config);

  bool initialize_client();
  bool initialize_server();
  bool initialize_server_from_packet(
      const std::vector<uint8_t>& initial_packet);

  // ngtcp2 コールバック
  static int client_initial_cb(ngtcp2_conn* conn, void* user_data);
  static int recv_crypto_data_cb(ngtcp2_conn* conn,
                                 ngtcp2_encryption_level encryption_level,
                                 uint64_t offset,
                                 const uint8_t* data,
                                 size_t datalen,
                                 void* user_data);
  static int encrypt_cb(uint8_t* dest,
                        const ngtcp2_crypto_aead* aead,
                        const ngtcp2_crypto_aead_ctx* aead_ctx,
                        const uint8_t* plaintext,
                        size_t plaintextlen,
                        const uint8_t* nonce,
                        size_t noncelen,
                        const uint8_t* aad,
                        size_t aadlen);
  static int decrypt_cb(uint8_t* dest,
                        const ngtcp2_crypto_aead* aead,
                        const ngtcp2_crypto_aead_ctx* aead_ctx,
                        const uint8_t* ciphertext,
                        size_t ciphertextlen,
                        const uint8_t* nonce,
                        size_t noncelen,
                        const uint8_t* aad,
                        size_t aadlen);
  static int hp_mask_cb(uint8_t* dest,
                        const ngtcp2_crypto_cipher* hp,
                        const ngtcp2_crypto_cipher_ctx* hp_ctx,
                        const uint8_t* sample);
  static int recv_stream_data_cb(ngtcp2_conn* conn,
                                 uint32_t flags,
                                 int64_t stream_id,
                                 uint64_t offset,
                                 const uint8_t* data,
                                 size_t datalen,
                                 void* user_data,
                                 void* stream_user_data);
  static int acked_stream_data_offset_cb(ngtcp2_conn* conn,
                                         int64_t stream_id,
                                         uint64_t offset,
                                         uint64_t datalen,
                                         void* user_data,
                                         void* stream_user_data);
  static int stream_open_cb(ngtcp2_conn* conn,
                            int64_t stream_id,
                            void* user_data);
  static int stream_close_cb(ngtcp2_conn* conn,
                             uint32_t flags,
                             int64_t stream_id,
                             uint64_t app_error_code,
                             void* user_data,
                             void* stream_user_data);
  static int stream_reset_cb(ngtcp2_conn* conn,
                             int64_t stream_id,
                             uint64_t final_size,
                             uint64_t app_error_code,
                             void* user_data,
                             void* stream_user_data);
  static int recv_datagram_cb(ngtcp2_conn* conn,
                              uint32_t flags,
                              const uint8_t* data,
                              size_t datalen,
                              void* user_data);
  static int handshake_completed_cb(ngtcp2_conn* conn, void* user_data);
  static void rand_cb(uint8_t* dest,
                      size_t destlen,
                      const ngtcp2_rand_ctx* rand_ctx);
  static int get_new_connection_id_cb(ngtcp2_conn* conn,
                                      ngtcp2_cid* cid,
                                      uint8_t* token,
                                      size_t cidlen,
                                      void* user_data);
  static int update_key_cb(ngtcp2_conn* conn,
                           uint8_t* rx_secret,
                           uint8_t* tx_secret,
                           ngtcp2_crypto_aead_ctx* rx_aead_ctx,
                           uint8_t* rx_iv,
                           ngtcp2_crypto_aead_ctx* tx_aead_ctx,
                           uint8_t* tx_iv,
                           const uint8_t* current_rx_secret,
                           const uint8_t* current_tx_secret,
                           size_t secretlen,
                           void* user_data);
  static int recv_retry_cb(ngtcp2_conn* conn,
                           const ngtcp2_pkt_hd* hd,
                           void* user_data);
  static void delete_crypto_aead_ctx_cb(ngtcp2_conn* conn,
                                        ngtcp2_crypto_aead_ctx* aead_ctx,
                                        void* user_data);
  static void delete_crypto_cipher_ctx_cb(ngtcp2_conn* conn,
                                          ngtcp2_crypto_cipher_ctx* cipher_ctx,
                                          void* user_data);
  static int get_path_challenge_data_cb(ngtcp2_conn* conn,
                                        uint8_t* data,
                                        void* user_data);
  static int version_negotiation_cb(ngtcp2_conn* conn,
                                    uint32_t version,
                                    const ngtcp2_cid* client_dcid,
                                    void* user_data);

  // ヘルパー
  void push_event(QuicEvent event);
  int write_streams();
  SSL_CTX* create_ssl_ctx();
  int setup_initial_crypto();

  bool is_server_;
  QuicConfig config_;
  ngtcp2_conn* conn_ = nullptr;
  SSL_CTX* ssl_ctx_ = nullptr;
  SSL* ssl_ = nullptr;
  ngtcp2_crypto_conn_ref conn_ref_;

  // イベントキュー
  std::deque<QuicEvent> events_;

  // ストリームデータ (送信待ち)
  std::map<int64_t, std::deque<StreamData>> stream_buffers_;

  // Datagram 送信キュー
  std::deque<std::vector<uint8_t>> datagram_queue_;

  // 接続状態
  bool handshake_completed_ = false;
  bool closed_ = false;

  // 現在時刻 (ナノ秒)
  uint64_t timestamp_ns_ = 0;

  // 送信バッファ
  std::vector<uint8_t> send_buffer_;
};

// Python バインディングを定義
void bind_quic(nb::module_& m);

}  // namespace quic
}  // namespace webtransport
