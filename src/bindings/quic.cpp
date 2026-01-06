/**
 * QUIC バインディング (ngtcp2 ラッパー)
 *
 * Sans-IO スタイルの QUIC 実装
 */

#include "quic.h"

#include <openssl/err.h>
#include <openssl/rand.h>

#include <chrono>
#include <climits>
#include <cstdint>
#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace quic {

namespace {

// 現在時刻をナノ秒で取得
uint64_t get_timestamp_ns() {
  auto now = std::chrono::steady_clock::now();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             now.time_since_epoch())
      .count();
}

// ALPN を構築
std::vector<uint8_t> build_alpn(const std::vector<std::string>& protocols) {
  std::vector<uint8_t> alpn;
  for (const auto& proto : protocols) {
    alpn.push_back(static_cast<uint8_t>(proto.size()));
    alpn.insert(alpn.end(), proto.begin(), proto.end());
  }
  return alpn;
}

}  // namespace

// ========== QuicConnection 実装 ==========

QuicConnection::QuicConnection(bool is_server, const QuicConfig& config)
    : is_server_(is_server), config_(config) {
  timestamp_ns_ = get_timestamp_ns();
  send_buffer_.resize(65536);  // 64KB バッファ
}

QuicConnection::~QuicConnection() {
  if (conn_) {
    ngtcp2_conn_del(conn_);
  }
  if (ssl_) {
    SSL_set_app_data(ssl_, nullptr);
    SSL_free(ssl_);
  }
  if (ssl_ctx_) {
    SSL_CTX_free(ssl_ctx_);
  }
}

QuicConnection::QuicConnection(QuicConnection&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      conn_(other.conn_),
      ssl_ctx_(other.ssl_ctx_),
      ssl_(other.ssl_),
      events_(std::move(other.events_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      datagram_queue_(std::move(other.datagram_queue_)),
      handshake_completed_(other.handshake_completed_),
      closed_(other.closed_),
      timestamp_ns_(other.timestamp_ns_),
      send_buffer_(std::move(other.send_buffer_)) {
  other.conn_ = nullptr;
  other.ssl_ctx_ = nullptr;
  other.ssl_ = nullptr;

  // conn_ref_ を新しいオブジェクトを指すように再設定
  if (conn_ != nullptr && ssl_ != nullptr) {
    conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
      auto* conn = static_cast<QuicConnection*>(ref->user_data);
      return conn->conn_;
    };
    conn_ref_.user_data = this;
    SSL_set_app_data(ssl_, &conn_ref_);
  }
}

QuicConnection& QuicConnection::operator=(QuicConnection&& other) noexcept {
  if (this != &other) {
    if (conn_)
      ngtcp2_conn_del(conn_);
    if (ssl_)
      SSL_free(ssl_);
    if (ssl_ctx_)
      SSL_CTX_free(ssl_ctx_);

    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    conn_ = other.conn_;
    ssl_ctx_ = other.ssl_ctx_;
    ssl_ = other.ssl_;
    events_ = std::move(other.events_);
    stream_buffers_ = std::move(other.stream_buffers_);
    datagram_queue_ = std::move(other.datagram_queue_);
    handshake_completed_ = other.handshake_completed_;
    closed_ = other.closed_;
    timestamp_ns_ = other.timestamp_ns_;
    send_buffer_ = std::move(other.send_buffer_);

    other.conn_ = nullptr;
    other.ssl_ctx_ = nullptr;
    other.ssl_ = nullptr;

    // conn_ref_ を新しいオブジェクトを指すように再設定
    if (conn_ != nullptr && ssl_ != nullptr) {
      conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
        auto* conn = static_cast<QuicConnection*>(ref->user_data);
        return conn->conn_;
      };
      conn_ref_.user_data = this;
      SSL_set_app_data(ssl_, &conn_ref_);
    }
  }
  return *this;
}

std::unique_ptr<QuicConnection> QuicConnection::create_client(
    const QuicConfig& config) {
  auto conn =
      std::unique_ptr<QuicConnection>(new QuicConnection(false, config));
  if (!conn->initialize_client()) {
    return nullptr;
  }
  return conn;
}

std::unique_ptr<QuicConnection> QuicConnection::create_server(
    const QuicConfig& config) {
  auto conn = std::unique_ptr<QuicConnection>(new QuicConnection(true, config));
  if (!conn->initialize_server()) {
    return nullptr;
  }
  return conn;
}

std::unique_ptr<QuicConnection> QuicConnection::accept(
    const QuicConfig& config,
    const std::vector<uint8_t>& initial_packet) {
  auto conn = std::unique_ptr<QuicConnection>(new QuicConnection(true, config));
  if (!conn->initialize_server_from_packet(initial_packet)) {
    return nullptr;
  }
  return conn;
}

SSL_CTX* QuicConnection::create_ssl_ctx() {
  const SSL_METHOD* method =
      is_server_ ? TLS_server_method() : TLS_client_method();
  SSL_CTX* ctx = SSL_CTX_new(method);
  if (!ctx) {
    return nullptr;
  }

  // TLS 1.3 のみ許可
  SSL_CTX_set_min_proto_version(ctx, TLS1_3_VERSION);
  SSL_CTX_set_max_proto_version(ctx, TLS1_3_VERSION);

  // QUIC 用の設定 (BoringSSL)
  uint64_t ssl_opts = (SSL_OP_ALL & ~SSL_OP_DONT_INSERT_EMPTY_FRAGMENTS) |
                      SSL_OP_SINGLE_ECDH_USE | SSL_OP_CIPHER_SERVER_PREFERENCE;
  SSL_CTX_set_options(ctx, ssl_opts);
  SSL_CTX_set_mode(ctx, SSL_MODE_RELEASE_BUFFERS);

  // 証明書検証の設定
  if (!is_server_ && !config_.verify_peer) {
    SSL_CTX_set_verify(ctx, SSL_VERIFY_NONE, nullptr);
  }

  if (is_server_) {
    // サーバー証明書の読み込み
    if (!config_.cert_file.empty()) {
      if (SSL_CTX_use_certificate_chain_file(ctx, config_.cert_file.c_str()) !=
          1) {
        SSL_CTX_free(ctx);
        return nullptr;
      }
    }
    if (!config_.key_file.empty()) {
      if (SSL_CTX_use_PrivateKey_file(ctx, config_.key_file.c_str(),
                                      SSL_FILETYPE_PEM) != 1) {
        SSL_CTX_free(ctx);
        return nullptr;
      }
    }
  }

  // ALPN の設定
  if (!config_.alpn.empty()) {
    auto alpn_data = build_alpn(config_.alpn);
    if (is_server_) {
      // サーバー側: クライアントの ALPN リストから一致するものを選択
      // コールバック内でクライアントのバッファを直接参照する
      SSL_CTX_set_alpn_select_cb(
          ctx,
          [](SSL*, const unsigned char** out, unsigned char* outlen,
             const unsigned char* in, unsigned int inlen, void* arg) -> int {
            auto* config = static_cast<QuicConfig*>(arg);

            // クライアントの ALPN リストを走査
            const unsigned char* client_alpn = in;
            const unsigned char* client_alpn_end = in + inlen;

            while (client_alpn < client_alpn_end) {
              unsigned char client_proto_len = *client_alpn;
              if (client_alpn + 1 + client_proto_len > client_alpn_end) {
                break;
              }

              // サーバーがサポートする各 ALPN と比較
              for (const auto& server_proto : config->alpn) {
                if (client_proto_len == server_proto.size() &&
                    memcmp(client_alpn + 1, server_proto.data(),
                           server_proto.size()) == 0) {
                  // 一致: クライアントのバッファ内のデータを指す
                  *out = client_alpn + 1;
                  *outlen = client_proto_len;
                  return SSL_TLSEXT_ERR_OK;
                }
              }

              client_alpn += 1 + client_proto_len;
            }

            return SSL_TLSEXT_ERR_NOACK;
          },
          &config_);
    } else {
      SSL_CTX_set_alpn_protos(ctx, alpn_data.data(),
                              static_cast<unsigned int>(alpn_data.size()));
    }
  }

  return ctx;
}

bool QuicConnection::initialize_client() {
  ssl_ctx_ = create_ssl_ctx();
  if (!ssl_ctx_) {
    return false;
  }

  // BoringSSL QUIC 設定を SSL_CTX に適用
  if (ngtcp2_crypto_boringssl_configure_client_context(ssl_ctx_) != 0) {
    return false;
  }

  ssl_ = SSL_new(ssl_ctx_);
  if (!ssl_) {
    return false;
  }

  // ngtcp2 コールバックの設定
  ngtcp2_callbacks callbacks{};
  callbacks.client_initial = client_initial_cb;
  callbacks.recv_crypto_data = recv_crypto_data_cb;
  callbacks.encrypt = encrypt_cb;
  callbacks.decrypt = decrypt_cb;
  callbacks.hp_mask = hp_mask_cb;
  callbacks.recv_stream_data = recv_stream_data_cb;
  callbacks.acked_stream_data_offset = acked_stream_data_offset_cb;
  callbacks.stream_open = stream_open_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.stream_reset = stream_reset_cb;
  callbacks.recv_datagram = recv_datagram_cb;
  callbacks.handshake_completed = handshake_completed_cb;
  callbacks.rand = rand_cb;
  callbacks.get_new_connection_id = get_new_connection_id_cb;
  callbacks.update_key = update_key_cb;
  callbacks.recv_retry = recv_retry_cb;
  callbacks.delete_crypto_aead_ctx = delete_crypto_aead_ctx_cb;
  callbacks.delete_crypto_cipher_ctx = delete_crypto_cipher_ctx_cb;
  callbacks.get_path_challenge_data = get_path_challenge_data_cb;
  callbacks.version_negotiation = version_negotiation_cb;

  // トランスポートパラメータの設定
  ngtcp2_settings settings;
  ngtcp2_settings_default(&settings);
  settings.initial_ts = timestamp_ns_;
  settings.log_printf = nullptr;

  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_streams_bidi = config_.max_streams_bidi;
  params.initial_max_streams_uni = config_.max_streams_uni;
  params.initial_max_data = config_.max_data;
  params.initial_max_stream_data_bidi_local =
      config_.max_stream_data_bidi_local;
  params.initial_max_stream_data_bidi_remote =
      config_.max_stream_data_bidi_remote;
  params.initial_max_stream_data_uni = config_.max_stream_data_uni;
  params.max_idle_timeout = config_.idle_timeout_ns;

  if (config_.enable_datagram) {
    params.max_datagram_frame_size = config_.max_datagram_frame_size;
  }

  // 接続 ID の生成
  ngtcp2_cid scid, dcid;
  scid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN;
  RAND_bytes(scid.data, scid.datalen);
  dcid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN;
  RAND_bytes(dcid.data, dcid.datalen);

  // パスの設定 (ダミー - 実際は Python 側で設定)
  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  int rv =
      ngtcp2_conn_client_new(&conn_, &dcid, &scid, &path, NGTCP2_PROTO_VER_V1,
                             &callbacks, &settings, &params, nullptr, this);
  if (rv != 0) {
    return false;
  }

  // conn_ref を設定
  conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
    auto* self = static_cast<QuicConnection*>(ref->user_data);
    return self->conn_;
  };
  conn_ref_.user_data = this;

  // SSL に conn_ref を設定
  SSL_set_app_data(ssl_, &conn_ref_);

  // SSL 接続状態を設定 (configure_client_session と SSL_set_app_data の後)
  SSL_set_connect_state(ssl_);

  // ALPN の設定 (SSL オブジェクトに設定)
  if (!config_.alpn.empty()) {
    auto alpn_data = build_alpn(config_.alpn);
    SSL_set_alpn_protos(ssl_, alpn_data.data(),
                        static_cast<unsigned int>(alpn_data.size()));
  }

  // SNI の設定
  if (!config_.server_name.empty()) {
    SSL_set_tlsext_host_name(ssl_, config_.server_name.c_str());
  }

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  return true;
}

bool QuicConnection::initialize_server() {
  ssl_ctx_ = create_ssl_ctx();
  if (!ssl_ctx_) {
    return false;
  }

  // BoringSSL QUIC 設定を SSL_CTX に適用
  if (ngtcp2_crypto_boringssl_configure_server_context(ssl_ctx_) != 0) {
    return false;
  }

  ssl_ = SSL_new(ssl_ctx_);
  if (!ssl_) {
    return false;
  }

  // ngtcp2 コールバックの設定
  ngtcp2_callbacks callbacks{};
  callbacks.recv_client_initial = ngtcp2_crypto_recv_client_initial_cb;
  callbacks.recv_crypto_data = recv_crypto_data_cb;
  callbacks.encrypt = encrypt_cb;
  callbacks.decrypt = decrypt_cb;
  callbacks.hp_mask = hp_mask_cb;
  callbacks.recv_stream_data = recv_stream_data_cb;
  callbacks.acked_stream_data_offset = acked_stream_data_offset_cb;
  callbacks.stream_open = stream_open_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.stream_reset = stream_reset_cb;
  callbacks.recv_datagram = recv_datagram_cb;
  callbacks.handshake_completed = handshake_completed_cb;
  callbacks.rand = rand_cb;
  callbacks.get_new_connection_id = get_new_connection_id_cb;
  callbacks.update_key = update_key_cb;
  callbacks.delete_crypto_aead_ctx = delete_crypto_aead_ctx_cb;
  callbacks.delete_crypto_cipher_ctx = delete_crypto_cipher_ctx_cb;
  callbacks.get_path_challenge_data = get_path_challenge_data_cb;
  callbacks.version_negotiation = version_negotiation_cb;

  // トランスポートパラメータの設定
  ngtcp2_settings settings;
  ngtcp2_settings_default(&settings);
  settings.initial_ts = timestamp_ns_;
  settings.log_printf = nullptr;

  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_streams_bidi = config_.max_streams_bidi;
  params.initial_max_streams_uni = config_.max_streams_uni;
  params.initial_max_data = config_.max_data;
  params.initial_max_stream_data_bidi_local =
      config_.max_stream_data_bidi_local;
  params.initial_max_stream_data_bidi_remote =
      config_.max_stream_data_bidi_remote;
  params.initial_max_stream_data_uni = config_.max_stream_data_uni;
  params.max_idle_timeout = config_.idle_timeout_ns;
  params.original_dcid_present = 1;

  if (config_.enable_datagram) {
    params.max_datagram_frame_size = config_.max_datagram_frame_size;
  }

  // 接続 ID の生成
  ngtcp2_cid scid, dcid;
  scid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN;
  RAND_bytes(scid.data, scid.datalen);
  dcid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN;
  RAND_bytes(dcid.data, dcid.datalen);

  // original_dcid を設定
  params.original_dcid = dcid;

  // パスの設定 (ダミー - 実際は Python 側で設定)
  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  int rv =
      ngtcp2_conn_server_new(&conn_, &dcid, &scid, &path, NGTCP2_PROTO_VER_V1,
                             &callbacks, &settings, &params, nullptr, this);
  if (rv != 0) {
    return false;
  }

  // conn_ref を設定
  conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
    auto* self = static_cast<QuicConnection*>(ref->user_data);
    return self->conn_;
  };
  conn_ref_.user_data = this;

  // SSL に conn_ref を設定
  SSL_set_app_data(ssl_, &conn_ref_);

  // SSL 接続状態を設定
  SSL_set_accept_state(ssl_);

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  return true;
}

bool QuicConnection::initialize_server_from_packet(
    const std::vector<uint8_t>& initial_packet) {
  // 初期パケットのヘッダーをデコード
  ngtcp2_pkt_hd hd;
  ngtcp2_ssize pktlen = ngtcp2_pkt_decode_hd_long(&hd, initial_packet.data(),
                                                  initial_packet.size());
  if (pktlen < 0) {
    return false;
  }

  // Initial パケットかどうか確認
  if (hd.type != NGTCP2_PKT_INITIAL) {
    return false;
  }

  ssl_ctx_ = create_ssl_ctx();
  if (!ssl_ctx_) {
    return false;
  }

  // BoringSSL QUIC 設定を SSL_CTX に適用
  if (ngtcp2_crypto_boringssl_configure_server_context(ssl_ctx_) != 0) {
    return false;
  }

  ssl_ = SSL_new(ssl_ctx_);
  if (!ssl_) {
    return false;
  }

  // ngtcp2 コールバックの設定
  ngtcp2_callbacks callbacks{};
  callbacks.recv_client_initial = ngtcp2_crypto_recv_client_initial_cb;
  callbacks.recv_crypto_data = recv_crypto_data_cb;
  callbacks.encrypt = encrypt_cb;
  callbacks.decrypt = decrypt_cb;
  callbacks.hp_mask = hp_mask_cb;
  callbacks.recv_stream_data = recv_stream_data_cb;
  callbacks.acked_stream_data_offset = acked_stream_data_offset_cb;
  callbacks.stream_open = stream_open_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.stream_reset = stream_reset_cb;
  callbacks.recv_datagram = recv_datagram_cb;
  callbacks.handshake_completed = handshake_completed_cb;
  callbacks.rand = rand_cb;
  callbacks.get_new_connection_id = get_new_connection_id_cb;
  callbacks.update_key = update_key_cb;
  callbacks.delete_crypto_aead_ctx = delete_crypto_aead_ctx_cb;
  callbacks.delete_crypto_cipher_ctx = delete_crypto_cipher_ctx_cb;
  callbacks.get_path_challenge_data = get_path_challenge_data_cb;
  callbacks.version_negotiation = version_negotiation_cb;

  // トランスポートパラメータの設定
  ngtcp2_settings settings;
  ngtcp2_settings_default(&settings);
  settings.initial_ts = timestamp_ns_;
  settings.log_printf = nullptr;

  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_streams_bidi = config_.max_streams_bidi;
  params.initial_max_streams_uni = config_.max_streams_uni;
  params.initial_max_data = config_.max_data;
  params.initial_max_stream_data_bidi_local =
      config_.max_stream_data_bidi_local;
  params.initial_max_stream_data_bidi_remote =
      config_.max_stream_data_bidi_remote;
  params.initial_max_stream_data_uni = config_.max_stream_data_uni;
  params.max_idle_timeout = config_.idle_timeout_ns;
  params.original_dcid_present = 1;

  if (config_.enable_datagram) {
    params.max_datagram_frame_size = config_.max_datagram_frame_size;
  }

  // サーバーの SCID を新しく生成
  // クライアントの SCID をサーバーの DCID として使用
  ngtcp2_cid scid, dcid;
  scid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN;
  RAND_bytes(scid.data, scid.datalen);
  dcid = hd.scid;

  // original_dcid をクライアントの DCID に設定
  params.original_dcid = hd.dcid;

  // パスの設定 (ダミー - 実際は Python 側で設定)
  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  int rv =
      ngtcp2_conn_server_new(&conn_, &dcid, &scid, &path, hd.version,
                             &callbacks, &settings, &params, nullptr, this);
  if (rv != 0) {
    return false;
  }

  // conn_ref を設定
  conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
    auto* self = static_cast<QuicConnection*>(ref->user_data);
    return self->conn_;
  };
  conn_ref_.user_data = this;

  // SSL に conn_ref を設定
  SSL_set_app_data(ssl_, &conn_ref_);

  // SSL 接続状態を設定
  SSL_set_accept_state(ssl_);

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  return true;
}

size_t QuicConnection::receive(const std::vector<uint8_t>& data) {
  if (!conn_ || closed_) {
    return 0;
  }

  // OpenSSL エラーキューをクリア
  ERR_clear_error();

  timestamp_ns_ = get_timestamp_ns();

  // パスの設定 (ダミー)
  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  ngtcp2_pkt_info pi{};
  int rv = ngtcp2_conn_read_pkt(conn_, &path, &pi, data.data(), data.size(),
                                timestamp_ns_);
  if (rv != 0) {
    switch (rv) {
      case NGTCP2_ERR_DRAINING:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "connection draining"});
        break;
      case NGTCP2_ERR_CLOSING:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "connection closing"});
        break;
      case NGTCP2_ERR_DROP_CONN:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "connection dropped"});
        break;
      case NGTCP2_ERR_RETRY:
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "retry required"});
        break;
      case NGTCP2_ERR_CRYPTO:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "crypto error"});
        break;
      case NGTCP2_ERR_DECRYPT:
        // 復号エラーは無視（パケット破棄）
        break;
      case NGTCP2_ERR_DISCARD_PKT:
        // パケット破棄は無視
        break;
      default:
        // その他のエラーは接続を閉じる
        if (rv < NGTCP2_ERR_FATAL) {
          closed_ = true;
          push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                      "fatal error: " + std::string(ngtcp2_strerror(rv))});
        }
        break;
    }
    return 0;
  }

  return data.size();
}

std::optional<std::vector<uint8_t>> QuicConnection::send() {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  // OpenSSL エラーキューをクリア
  ERR_clear_error();

  timestamp_ns_ = get_timestamp_ns();

  // パスの設定 (ダミー)
  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  ngtcp2_pkt_info pi{};
  ngtcp2_ssize nwrite = 0;

  // まずストリームデータを書き込む
  for (auto& [stream_id, buffers] : stream_buffers_) {
    while (!buffers.empty()) {
      auto& buf = buffers.front();
      ngtcp2_vec vec;
      vec.base = const_cast<uint8_t*>(buf.data.data());
      vec.len = buf.data.size();

      uint32_t flags = 0;
      if (buf.fin) {
        flags |= NGTCP2_WRITE_STREAM_FLAG_FIN;
      }
      // 同じストリームに次のバッファがある、または datagram がある場合は MORE を設定
      if (buffers.size() > 1 || !datagram_queue_.empty()) {
        flags |= NGTCP2_WRITE_STREAM_FLAG_MORE;
      }
      ngtcp2_ssize ndatalen = 0;

      nwrite = ngtcp2_conn_writev_stream(conn_, &path, &pi, send_buffer_.data(),
                                         send_buffer_.size(), &ndatalen, flags,
                                         stream_id, &vec, 1, timestamp_ns_);
      if (nwrite < 0) {
        switch (nwrite) {
          case NGTCP2_ERR_WRITE_MORE:
            if (ndatalen > 0) {
              buf.data.erase(buf.data.begin(), buf.data.begin() + ndatalen);
              if (buf.data.empty()) {
                buffers.pop_front();
              }
            }
            continue;
          case NGTCP2_ERR_STREAM_DATA_BLOCKED:
            // フロー制御でブロックされた場合は次のストリームへ
            break;
          case NGTCP2_ERR_STREAM_SHUT_WR:
            // ストリームがハーフクローズされた場合はバッファをクリア
            buffers.clear();
            break;
          case NGTCP2_ERR_STREAM_NOT_FOUND:
            // ストリームが存在しない場合はバッファをクリア
            buffers.clear();
            break;
          default:
            break;
        }
        break;
      }

      if (ndatalen > 0) {
        buf.data.erase(buf.data.begin(), buf.data.begin() + ndatalen);
        if (buf.data.empty()) {
          buffers.pop_front();
        }
      }

      if (nwrite > 0) {
        return std::vector<uint8_t>(send_buffer_.begin(),
                                    send_buffer_.begin() + nwrite);
      }
    }
  }

  // Datagram を書き込む
  while (!datagram_queue_.empty()) {
    auto& dgram = datagram_queue_.front();
    int accepted = 0;

    ngtcp2_vec vec;
    vec.base = const_cast<uint8_t*>(dgram.data());
    vec.len = dgram.size();

    nwrite = ngtcp2_conn_writev_datagram(
        conn_, &path, &pi, send_buffer_.data(), send_buffer_.size(), &accepted,
        NGTCP2_WRITE_DATAGRAM_FLAG_MORE, 0, &vec, 1, timestamp_ns_);
    if (nwrite < 0) {
      if (nwrite == NGTCP2_ERR_WRITE_MORE) {
        if (accepted) {
          datagram_queue_.pop_front();
        }
        continue;
      }
      break;
    }

    if (accepted) {
      datagram_queue_.pop_front();
    }

    if (nwrite > 0) {
      return std::vector<uint8_t>(send_buffer_.begin(),
                                  send_buffer_.begin() + nwrite);
    }
  }

  // 通常のパケット (ACK など)
  nwrite = ngtcp2_conn_write_pkt(conn_, &path, &pi, send_buffer_.data(),
                                 send_buffer_.size(), timestamp_ns_);
  if (nwrite > 0) {
    return std::vector<uint8_t>(send_buffer_.begin(),
                                send_buffer_.begin() + nwrite);
  }

  return std::nullopt;
}

std::optional<uint64_t> QuicConnection::get_timeout_ns() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  ngtcp2_tstamp expiry = ngtcp2_conn_get_expiry(conn_);
  if (expiry == UINT64_MAX) {
    return std::nullopt;
  }

  uint64_t now = get_timestamp_ns();
  if (expiry <= now) {
    return 0;
  }

  return expiry - now;
}

void QuicConnection::handle_timeout() {
  if (!conn_ || closed_) {
    return;
  }

  timestamp_ns_ = get_timestamp_ns();
  int rv = ngtcp2_conn_handle_expiry(conn_, timestamp_ns_);
  if (rv != 0) {
    switch (rv) {
      case NGTCP2_ERR_IDLE_CLOSE:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "idle timeout"});
        break;
      case NGTCP2_ERR_HANDSHAKE_TIMEOUT:
        closed_ = true;
        push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                    "handshake timeout"});
        break;
      default:
        if (rv < NGTCP2_ERR_FATAL) {
          closed_ = true;
          push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                      "timeout error: " + std::string(ngtcp2_strerror(rv))});
        }
        break;
    }
  }
}

int64_t QuicConnection::open_stream(bool bidirectional) {
  if (!conn_ || closed_ || !handshake_completed_) {
    return -1;
  }

  int64_t stream_id = -1;
  int rv;

  if (bidirectional) {
    rv = ngtcp2_conn_open_bidi_stream(conn_, &stream_id, nullptr);
  } else {
    rv = ngtcp2_conn_open_uni_stream(conn_, &stream_id, nullptr);
  }

  if (rv != 0) {
    return -1;
  }

  stream_buffers_[stream_id] = {};
  return stream_id;
}

void QuicConnection::send_stream_data(int64_t stream_id,
                                      const std::vector<uint8_t>& data,
                                      bool fin) {
  if (!conn_ || closed_) {
    return;
  }

  stream_buffers_[stream_id].push_back({data, fin});
}

void QuicConnection::close_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_ || closed_) {
    return;
  }

  int rv = ngtcp2_conn_shutdown_stream(conn_, 0, stream_id, error_code);
  if (rv != 0 && rv != NGTCP2_ERR_STREAM_NOT_FOUND) {
    // STREAM_NOT_FOUND は無視（すでにクローズされている）
    // 致命的エラーの場合のみ接続を閉じる
    if (rv < NGTCP2_ERR_FATAL) {
      closed_ = true;
      push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                  "stream shutdown error: " + std::string(ngtcp2_strerror(rv))});
    }
  }
  stream_buffers_.erase(stream_id);
}

void QuicConnection::send_datagram(const std::vector<uint8_t>& data) {
  if (!conn_ || closed_ || !config_.enable_datagram) {
    return;
  }

  datagram_queue_.push_back(data);
}

void QuicConnection::close(uint64_t error_code, const std::string& reason) {
  if (!conn_ || closed_) {
    return;
  }

  timestamp_ns_ = get_timestamp_ns();

  ngtcp2_path path;
  sockaddr_storage local_addr{}, remote_addr{};
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  path.local.addr = reinterpret_cast<sockaddr*>(&local_addr);
  path.local.addrlen = sizeof(sockaddr_in);
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote_addr);
  path.remote.addrlen = sizeof(sockaddr_in);

  ngtcp2_pkt_info pi{};
  ngtcp2_ccerr ccerr;
  ngtcp2_ccerr_set_application_error(
      &ccerr, error_code, reinterpret_cast<const uint8_t*>(reason.c_str()),
      reason.size());

  ngtcp2_conn_write_connection_close(conn_, &path, &pi, send_buffer_.data(),
                                     send_buffer_.size(), &ccerr,
                                     timestamp_ns_);

  closed_ = true;
}

std::optional<QuicEvent> QuicConnection::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  auto event = std::move(events_.front());
  events_.pop_front();
  return event;
}

bool QuicConnection::is_established() const {
  return conn_ && handshake_completed_ && !closed_;
}

bool QuicConnection::is_closed() const {
  return closed_;
}

bool QuicConnection::is_handshake_completed() const {
  return handshake_completed_;
}

std::vector<uint8_t> QuicConnection::get_connection_id() const {
  if (!conn_) {
    return {};
  }

  auto* dcid = ngtcp2_conn_get_dcid(conn_);
  return std::vector<uint8_t>(dcid->data, dcid->data + dcid->datalen);
}

void QuicConnection::push_event(QuicEvent event) {
  events_.push_back(std::move(event));
}

// ========== ngtcp2 コールバック ==========

int QuicConnection::client_initial_cb(ngtcp2_conn* conn, void* user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);
  return ngtcp2_crypto_client_initial_cb(conn, user_data);
}

int QuicConnection::recv_crypto_data_cb(
    ngtcp2_conn* conn,
    ngtcp2_encryption_level encryption_level,
    uint64_t offset,
    const uint8_t* data,
    size_t datalen,
    void* user_data) {
  return ngtcp2_crypto_recv_crypto_data_cb(conn, encryption_level, offset, data,
                                           datalen, user_data);
}

int QuicConnection::encrypt_cb(uint8_t* dest,
                               const ngtcp2_crypto_aead* aead,
                               const ngtcp2_crypto_aead_ctx* aead_ctx,
                               const uint8_t* plaintext,
                               size_t plaintextlen,
                               const uint8_t* nonce,
                               size_t noncelen,
                               const uint8_t* aad,
                               size_t aadlen) {
  return ngtcp2_crypto_encrypt_cb(dest, aead, aead_ctx, plaintext, plaintextlen,
                                  nonce, noncelen, aad, aadlen);
}

int QuicConnection::decrypt_cb(uint8_t* dest,
                               const ngtcp2_crypto_aead* aead,
                               const ngtcp2_crypto_aead_ctx* aead_ctx,
                               const uint8_t* ciphertext,
                               size_t ciphertextlen,
                               const uint8_t* nonce,
                               size_t noncelen,
                               const uint8_t* aad,
                               size_t aadlen) {
  return ngtcp2_crypto_decrypt_cb(dest, aead, aead_ctx, ciphertext,
                                  ciphertextlen, nonce, noncelen, aad, aadlen);
}

int QuicConnection::hp_mask_cb(uint8_t* dest,
                               const ngtcp2_crypto_cipher* hp,
                               const ngtcp2_crypto_cipher_ctx* hp_ctx,
                               const uint8_t* sample) {
  return ngtcp2_crypto_hp_mask_cb(dest, hp, hp_ctx, sample);
}

int QuicConnection::recv_stream_data_cb(ngtcp2_conn* conn,
                                        uint32_t flags,
                                        int64_t stream_id,
                                        uint64_t offset,
                                        const uint8_t* data,
                                        size_t datalen,
                                        void* user_data,
                                        void* stream_user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);
  bool fin = (flags & NGTCP2_STREAM_DATA_FLAG_FIN) != 0;

  QuicEvent event;
  event.type = QuicEventType::StreamData;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + datalen);
  event.fin = fin;
  self->push_event(std::move(event));

  return 0;
}

int QuicConnection::acked_stream_data_offset_cb(ngtcp2_conn* conn,
                                                int64_t stream_id,
                                                uint64_t offset,
                                                uint64_t datalen,
                                                void* user_data,
                                                void* stream_user_data) {
  // ACK されたデータのオフセットを処理
  return 0;
}

int QuicConnection::stream_open_cb(ngtcp2_conn* conn,
                                   int64_t stream_id,
                                   void* user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);

  QuicEvent event;
  event.type = QuicEventType::StreamOpened;
  event.stream_id = stream_id;
  self->push_event(std::move(event));

  self->stream_buffers_[stream_id] = {};
  return 0;
}

int QuicConnection::stream_close_cb(ngtcp2_conn* conn,
                                    uint32_t flags,
                                    int64_t stream_id,
                                    uint64_t app_error_code,
                                    void* user_data,
                                    void* stream_user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);

  QuicEvent event;
  event.type = QuicEventType::StreamClosed;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));

  self->stream_buffers_.erase(stream_id);
  return 0;
}

int QuicConnection::stream_reset_cb(ngtcp2_conn* conn,
                                    int64_t stream_id,
                                    uint64_t final_size,
                                    uint64_t app_error_code,
                                    void* user_data,
                                    void* stream_user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);

  QuicEvent event;
  event.type = QuicEventType::StreamReset;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));

  return 0;
}

int QuicConnection::recv_datagram_cb(ngtcp2_conn* conn,
                                     uint32_t flags,
                                     const uint8_t* data,
                                     size_t datalen,
                                     void* user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);

  QuicEvent event;
  event.type = QuicEventType::DatagramReceived;
  event.data = std::vector<uint8_t>(data, data + datalen);
  self->push_event(std::move(event));

  return 0;
}

int QuicConnection::handshake_completed_cb(ngtcp2_conn* conn, void* user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);
  self->handshake_completed_ = true;

  QuicEvent event;
  event.type = QuicEventType::HandshakeCompleted;
  self->push_event(std::move(event));

  return 0;
}

void QuicConnection::rand_cb(uint8_t* dest,
                             size_t destlen,
                             const ngtcp2_rand_ctx* rand_ctx) {
  RAND_bytes(dest, destlen);
}

int QuicConnection::get_new_connection_id_cb(ngtcp2_conn* conn,
                                             ngtcp2_cid* cid,
                                             uint8_t* token,
                                             size_t cidlen,
                                             void* user_data) {
  cid->datalen = cidlen;
  RAND_bytes(cid->data, cidlen);
  RAND_bytes(token, NGTCP2_STATELESS_RESET_TOKENLEN);
  return 0;
}

int QuicConnection::update_key_cb(ngtcp2_conn* conn,
                                  uint8_t* rx_secret,
                                  uint8_t* tx_secret,
                                  ngtcp2_crypto_aead_ctx* rx_aead_ctx,
                                  uint8_t* rx_iv,
                                  ngtcp2_crypto_aead_ctx* tx_aead_ctx,
                                  uint8_t* tx_iv,
                                  const uint8_t* current_rx_secret,
                                  const uint8_t* current_tx_secret,
                                  size_t secretlen,
                                  void* user_data) {
  return ngtcp2_crypto_update_key_cb(
      conn, rx_secret, tx_secret, rx_aead_ctx, rx_iv, tx_aead_ctx, tx_iv,
      current_rx_secret, current_tx_secret, secretlen, user_data);
}

int QuicConnection::recv_retry_cb(ngtcp2_conn* conn,
                                  const ngtcp2_pkt_hd* hd,
                                  void* user_data) {
  return ngtcp2_crypto_recv_retry_cb(conn, hd, user_data);
}

void QuicConnection::delete_crypto_aead_ctx_cb(ngtcp2_conn* conn,
                                               ngtcp2_crypto_aead_ctx* aead_ctx,
                                               void* user_data) {
  ngtcp2_crypto_delete_crypto_aead_ctx_cb(conn, aead_ctx, user_data);
}

void QuicConnection::delete_crypto_cipher_ctx_cb(
    ngtcp2_conn* conn,
    ngtcp2_crypto_cipher_ctx* cipher_ctx,
    void* user_data) {
  ngtcp2_crypto_delete_crypto_cipher_ctx_cb(conn, cipher_ctx, user_data);
}

int QuicConnection::get_path_challenge_data_cb(ngtcp2_conn* conn,
                                               uint8_t* data,
                                               void* user_data) {
  RAND_bytes(data, NGTCP2_PATH_CHALLENGE_DATALEN);
  return 0;
}

int QuicConnection::version_negotiation_cb(ngtcp2_conn* conn,
                                           uint32_t version,
                                           const ngtcp2_cid* client_dcid,
                                           void* user_data) {
  return ngtcp2_crypto_version_negotiation_cb(conn, version, client_dcid,
                                              user_data);
}

// ========== Python バインディング ==========

void bind_quic(nb::module_& m) {
  auto quic_m = m.def_submodule("quic", "QUIC protocol (ngtcp2)");

  // QuicConfig
  nb::class_<QuicConfig>(quic_m, "Config", "QUIC コネクション設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("max_streams_bidi", &QuicConfig::max_streams_bidi,
              "最大双方向ストリーム数")
      .def_rw("max_streams_uni", &QuicConfig::max_streams_uni,
              "最大単方向ストリーム数")
      .def_rw("max_data", &QuicConfig::max_data, "最大データサイズ")
      .def_rw("max_stream_data_bidi_local",
              &QuicConfig::max_stream_data_bidi_local,
              "ローカル双方向ストリームの最大データサイズ")
      .def_rw("max_stream_data_bidi_remote",
              &QuicConfig::max_stream_data_bidi_remote,
              "リモート双方向ストリームの最大データサイズ")
      .def_rw("max_stream_data_uni", &QuicConfig::max_stream_data_uni,
              "単方向ストリームの最大データサイズ")
      .def_rw("idle_timeout_ns", &QuicConfig::idle_timeout_ns,
              "アイドルタイムアウト (ナノ秒)")
      .def_rw("alpn_protocols", &QuicConfig::alpn, "ALPN プロトコル")
      .def_rw("server_name", &QuicConfig::server_name, "サーバー名 (SNI)")
      .def_rw("cert_file", &QuicConfig::cert_file, "証明書ファイルパス")
      .def_rw("key_file", &QuicConfig::key_file, "秘密鍵ファイルパス")
      .def_rw("verify_peer", &QuicConfig::verify_peer, "ピア検証を行うか")
      .def_rw("enable_datagram", &QuicConfig::enable_datagram,
              "Datagram を有効にするか")
      .def_rw("max_datagram_frame_size", &QuicConfig::max_datagram_frame_size,
              "最大 Datagram フレームサイズ");

  // QuicEventType
  nb::enum_<QuicEventType>(quic_m, "EventType", "QUIC イベント種別")
      .value("HANDSHAKE_COMPLETED", QuicEventType::HandshakeCompleted)
      .value("CONNECTION_CLOSED", QuicEventType::ConnectionClosed)
      .value("STREAM_DATA", QuicEventType::StreamData)
      .value("STREAM_OPENED", QuicEventType::StreamOpened)
      .value("STREAM_CLOSED", QuicEventType::StreamClosed)
      .value("STREAM_RESET", QuicEventType::StreamReset)
      .value("DATAGRAM", QuicEventType::DatagramReceived)
      .value("CONNECTION_ID_RETIRED", QuicEventType::ConnectionIdRetired);

  // QuicEvent
  nb::class_<QuicEvent>(quic_m, "Event", "QUIC イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &QuicEvent::type, "イベント種別")
      .def_ro("stream_id", &QuicEvent::stream_id, "ストリーム ID")
      .def_prop_ro(
          "data",
          [](const QuicEvent& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "データ")
      .def_ro("fin", &QuicEvent::fin, "FIN フラグ")
      .def_ro("error_code", &QuicEvent::error_code, "エラーコード")
      .def_ro("reason", &QuicEvent::reason, "理由");

  // QuicConnection
  nb::class_<QuicConnection>(quic_m, "Connection",
                             "QUIC コネクション (Sans-IO)")
      .def_static(
          "create_client",
          [](const QuicConfig& config) {
            auto conn = QuicConnection::create_client(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create QUIC client connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Connection"),
          "クライアントとして接続を作成")
      .def_static(
          "create_server",
          [](const QuicConfig& config) {
            auto conn = QuicConnection::create_server(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create QUIC server connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Connection"),
          "サーバーとして接続を作成")
      .def_static(
          "accept",
          [](const QuicConfig& config, nb::bytes initial_packet) {
            auto conn = QuicConnection::accept(
                config, std::vector<uint8_t>(
                            initial_packet.c_str(),
                            initial_packet.c_str() + initial_packet.size()));
            if (!conn) {
              throw std::runtime_error(
                  "Failed to accept QUIC connection from initial packet");
            }
            return conn.release();
          },
          nb::arg("config"), nb::arg("initial_packet"),
          nb::rv_policy::take_ownership,
          nb::sig("def accept(config: Config, initial_packet: bytes) -> "
                  "Connection"),
          "初期パケットからサーバー接続を作成")
      .def(
          "receive",
          [](QuicConnection& self, nb::bytes data) {
            return self.receive(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"), nb::sig("def receive(self, data: bytes) -> int"),
          "受信したデータを処理")
      .def(
          "send",
          [](QuicConnection& self) -> nb::object {
            auto result = self.send();
            if (result) {
              return nb::bytes(reinterpret_cast<const char*>(result->data()),
                               result->size());
            }
            return nb::none();
          },
          nb::sig("def send(self) -> bytes | None"), "送信すべきデータを取得")
      .def("get_timeout", &QuicConnection::get_timeout_ns,
           nb::sig("def get_timeout(self) -> int | None"),
           "次のタイムアウトまでの時間を取得 (ナノ秒)")
      .def("handle_timeout", &QuicConnection::handle_timeout,
           nb::sig("def handle_timeout(self) -> None"), "タイムアウトを処理")
      .def("open_stream", &QuicConnection::open_stream,
           nb::arg("bidirectional") = true,
           nb::sig("def open_stream(self, bidirectional: bool = True) -> int"),
           "ストリームを開く")
      .def(
          "send_stream_data",
          [](QuicConnection& self, int64_t stream_id, nb::bytes data,
             bool fin) {
            self.send_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def send_stream_data(self, stream_id: int, data: bytes, "
                  "fin: bool = False) -> None"),
          "ストリームにデータを送信")
      .def("close_stream", &QuicConnection::close_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def close_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "ストリームを閉じる")
      .def(
          "send_datagram",
          [](QuicConnection& self, nb::bytes data) {
            self.send_datagram(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"),
          nb::sig("def send_datagram(self, data: bytes) -> None"),
          "Datagram を送信")
      .def(
          "close", &QuicConnection::close, nb::arg("error_code") = 0,
          nb::arg("reason") = "",
          nb::sig(
              "def close(self, error_code: int = 0, reason: str = '') -> None"),
          "接続を閉じる")
      .def("next_event", &QuicConnection::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("is_established", &QuicConnection::is_established,
           nb::sig("def is_established(self) -> bool"),
           "接続が確立されているか")
      .def("is_closed", &QuicConnection::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def("is_handshake_completed", &QuicConnection::is_handshake_completed,
           nb::sig("def is_handshake_completed(self) -> bool"),
           "ハンドシェイクが完了したか")
      .def(
          "get_connection_id",
          [](const QuicConnection& self) {
            auto cid = self.get_connection_id();
            return nb::bytes(reinterpret_cast<const char*>(cid.data()),
                             cid.size());
          },
          nb::sig("def get_connection_id(self) -> bytes"), "接続 ID を取得");

  // ngtcp2 バージョン情報
  quic_m.def(
      "get_version",
      []() {
        auto* ver = ngtcp2_version(0);
        return std::string(ver->version_str);
      },
      nb::sig("def get_version() -> str"), "ngtcp2 のバージョンを取得");
}

}  // namespace quic
}  // namespace webtransport
