/**
 * QUIC バインディング (ngtcp2 ラッパー)
 *
 * Sans-IO スタイルの QUIC 実装
 */

#include "quic.h"

#include <openssl/err.h>
#include <openssl/pool.h>
#include <openssl/rand.h>
#include <openssl/x509.h>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>

#include <array>
#include <chrono>
#include <climits>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>

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

// host+port から sockaddr_storage を埋める (IPv4 / IPv6 / ホスト名)
bool fill_sockaddr(sockaddr_storage* storage,
                   socklen_t* addrlen,
                   const std::string& host,
                   uint16_t port) {
  std::memset(storage, 0, sizeof(*storage));

  // IPv4 リテラル
  auto* in4 = reinterpret_cast<sockaddr_in*>(storage);
  if (inet_pton(AF_INET, host.c_str(), &in4->sin_addr) == 1) {
    in4->sin_family = AF_INET;
    in4->sin_port = htons(port);
    *addrlen = sizeof(sockaddr_in);
    return true;
  }

  // IPv6 リテラル
  auto* in6 = reinterpret_cast<sockaddr_in6*>(storage);
  if (inet_pton(AF_INET6, host.c_str(), &in6->sin6_addr) == 1) {
    in6->sin6_family = AF_INET6;
    in6->sin6_port = htons(port);
    *addrlen = sizeof(sockaddr_in6);
    return true;
  }

  // ホスト名解決
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_DGRAM;
  addrinfo* result = nullptr;
  std::string port_str = std::to_string(port);
  if (getaddrinfo(host.c_str(), port_str.c_str(), &hints, &result) != 0 ||
      result == nullptr) {
    return false;
  }

  std::memcpy(storage, result->ai_addr, result->ai_addrlen);
  *addrlen = static_cast<socklen_t>(result->ai_addrlen);
  freeaddrinfo(result);
  return true;
}

// sockaddr から host+port 文字列を抽出する
bool sockaddr_to_host_port(const sockaddr* addr,
                           socklen_t addrlen,
                           std::string* host,
                           uint16_t* port) {
  char host_buf[NI_MAXHOST];
  char port_buf[NI_MAXSERV];
  if (getnameinfo(addr, addrlen, host_buf, sizeof(host_buf), port_buf,
                  sizeof(port_buf), NI_NUMERICHOST | NI_NUMERICSERV) != 0) {
    return false;
  }
  *host = host_buf;
  *port = static_cast<uint16_t>(std::stoi(port_buf));
  return true;
}

// パスを "local -> remote" 形式の文字列にする
std::string format_path_reason(const ngtcp2_path* path) {
  std::string local_host;
  std::string remote_host;
  uint16_t local_port = 0;
  uint16_t remote_port = 0;
  sockaddr_to_host_port(path->local.addr, path->local.addrlen, &local_host,
                        &local_port);
  sockaddr_to_host_port(path->remote.addr, path->remote.addrlen, &remote_host,
                        &remote_port);
  return local_host + ":" + std::to_string(local_port) + " -> " + remote_host +
         ":" + std::to_string(remote_port);
}

}  // namespace

// ========== QuicConnection 実装 ==========

QuicConnection::QuicConnection(bool is_server, const QuicConfig& config)
    : is_server_(is_server), config_(config) {
  timestamp_ns_ = get_timestamp_ns();
  send_buffer_.resize(65536);  // 64KB バッファ

  // 初期パスは空の IPv4 アドレス
  auto* local_in = reinterpret_cast<sockaddr_in*>(&local_addr_);
  auto* remote_in = reinterpret_cast<sockaddr_in*>(&remote_addr_);
  local_in->sin_family = AF_INET;
  remote_in->sin_family = AF_INET;
  local_addrlen_ = sizeof(sockaddr_in);
  remote_addrlen_ = sizeof(sockaddr_in);
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

void QuicConnection::rebind_conn_ref() {
  if (conn_ != nullptr && ssl_ != nullptr) {
    conn_ref_.get_conn = [](ngtcp2_crypto_conn_ref* ref) -> ngtcp2_conn* {
      auto* conn = static_cast<QuicConnection*>(ref->user_data);
      return conn->conn_;
    };
    conn_ref_.user_data = this;
    SSL_set_app_data(ssl_, &conn_ref_);
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
      early_data_attempted_(other.early_data_attempted_),
      early_data_rejected_event_pushed_(
          other.early_data_rejected_event_pushed_),
      last_session_ticket_(std::move(other.last_session_ticket_)),
      local_addr_(other.local_addr_),
      local_addrlen_(other.local_addrlen_),
      remote_addr_(other.remote_addr_),
      remote_addrlen_(other.remote_addrlen_),
      timestamp_ns_(other.timestamp_ns_),
      send_buffer_(std::move(other.send_buffer_)) {
  other.conn_ = nullptr;
  other.ssl_ctx_ = nullptr;
  other.ssl_ = nullptr;
  rebind_conn_ref();
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
    early_data_attempted_ = other.early_data_attempted_;
    early_data_rejected_event_pushed_ =
        other.early_data_rejected_event_pushed_;
    last_session_ticket_ = std::move(other.last_session_ticket_);
    local_addr_ = other.local_addr_;
    local_addrlen_ = other.local_addrlen_;
    remote_addr_ = other.remote_addr_;
    remote_addrlen_ = other.remote_addrlen_;
    timestamp_ns_ = other.timestamp_ns_;
    send_buffer_ = std::move(other.send_buffer_);

    other.conn_ = nullptr;
    other.ssl_ctx_ = nullptr;
    other.ssl_ = nullptr;

    rebind_conn_ref();
  }
  return *this;
}

std::unique_ptr<QuicConnection> QuicConnection::create_client(
    const QuicConfig& config,
    const std::string& local_host,
    uint16_t local_port,
    const std::string& remote_host,
    uint16_t remote_port) {
  auto conn =
      std::unique_ptr<QuicConnection>(new QuicConnection(false, config));
  if (!conn->initialize_client(local_host, local_port, remote_host,
                               remote_port)) {
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
    const std::vector<uint8_t>& initial_packet,
    const std::string& local_host,
    uint16_t local_port,
    const std::string& remote_host,
    uint16_t remote_port) {
  auto conn = std::unique_ptr<QuicConnection>(new QuicConnection(true, config));
  if (!conn->initialize_server_from_packet(initial_packet, local_host,
                                           local_port, remote_host,
                                           remote_port)) {
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

  if (!is_server_) {
    // クライアント: セッションチケット受信用キャッシュ設定
    SSL_CTX_set_session_cache_mode(
        ctx, SSL_SESS_CACHE_CLIENT | SSL_SESS_CACHE_NO_INTERNAL);
    SSL_CTX_sess_set_new_cb(ctx, new_session_cb);

    // 証明書検証の設定 (カスタムコールバックは SSL 単位で後から設定)
    if (!config_.verify_callback) {
      if (!config_.verify_peer) {
        SSL_CTX_set_verify(ctx, SSL_VERIFY_NONE, nullptr);
      } else {
        SSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, nullptr);
        if (!config_.ca_file.empty()) {
          if (SSL_CTX_load_verify_locations(ctx, config_.ca_file.c_str(),
                                            nullptr) != 1) {
            SSL_CTX_free(ctx);
            return nullptr;
          }
        } else {
          if (SSL_CTX_set_default_verify_paths(ctx) != 1) {
            SSL_CTX_free(ctx);
            return nullptr;
          }
        }
      }
    }
  }

  if (is_server_) {
    // 接続ごとに SSL_CTX を作るため、セッションチケット鍵をプロセス内で共有する。
    // 共有しないと再接続時にチケットを復号できず Resumption / 0-RTT が失敗する。
    static std::array<uint8_t, 48> ticket_keys{};
    static std::once_flag ticket_keys_once;
    std::call_once(ticket_keys_once, []() {
      RAND_bytes(ticket_keys.data(), ticket_keys.size());
    });
    if (SSL_CTX_set_tlsext_ticket_keys(ctx, ticket_keys.data(),
                                       ticket_keys.size()) != 1) {
      SSL_CTX_free(ctx);
      return nullptr;
    }

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

bool QuicConnection::setup_server_early_data() {
  if (!config_.enable_early_data) {
    return true;
  }

  SSL_set_early_data_enabled(ssl_, 1);

  // 設定のフロー制御上限を early data context に符号化する
  std::array<uint8_t, 256> quic_early_data_ctx{};
  ngtcp2_transport_params params;
  ngtcp2_transport_params_default(&params);
  params.initial_max_streams_bidi = config_.max_streams_bidi;
  params.initial_max_streams_uni = config_.max_streams_uni;
  params.initial_max_stream_data_bidi_local =
      config_.max_stream_data_bidi_local;
  params.initial_max_stream_data_bidi_remote =
      config_.max_stream_data_bidi_remote;
  params.initial_max_stream_data_uni = config_.max_stream_data_uni;
  params.initial_max_data = config_.max_data;

  ngtcp2_ssize quic_early_data_ctxlen = ngtcp2_transport_params_encode(
      quic_early_data_ctx.data(), quic_early_data_ctx.size(), &params);
  if (quic_early_data_ctxlen < 0) {
    return false;
  }

  if (SSL_set_quic_early_data_context(
          ssl_, quic_early_data_ctx.data(),
          static_cast<size_t>(quic_early_data_ctxlen)) != 1) {
    return false;
  }

  return true;
}

bool QuicConnection::setup_client_session() {
  // カスタム証明書検証
  if (config_.verify_callback) {
    SSL_set_custom_verify(ssl_, SSL_VERIFY_PEER, custom_verify_cb);
  }

  // SNI の設定
  if (!config_.server_name.empty()) {
    SSL_set_tlsext_host_name(ssl_, config_.server_name.c_str());
  }

  // ピア名検証 (verify_peer かつ server_name がある場合)
  // IP リテラルは DNS 名検証 (SSL_set1_host) では SAN の IPAddress と
  // 一致しないため、X509_VERIFY_PARAM_set1_ip_asc を使う。
  if (config_.verify_peer && !config_.server_name.empty() &&
      !config_.verify_callback) {
    in_addr ipv4{};
    in6_addr ipv6{};
    const bool is_ip = inet_pton(AF_INET, config_.server_name.c_str(), &ipv4) ==
                           1 ||
                       inet_pton(AF_INET6, config_.server_name.c_str(), &ipv6) ==
                           1;
    if (is_ip) {
      X509_VERIFY_PARAM* param = SSL_get0_param(ssl_);
      if (param == nullptr ||
          X509_VERIFY_PARAM_set1_ip_asc(param, config_.server_name.c_str()) !=
              1) {
        return false;
      }
    } else if (SSL_set1_host(ssl_, config_.server_name.c_str()) != 1) {
      return false;
    }
  }

  // セッションチケット import
  if (!config_.session_ticket.empty()) {
    const uint8_t* pointer = config_.session_ticket.data();
    SSL_SESSION* session = d2i_SSL_SESSION(
        nullptr, &pointer,
        static_cast<long>(config_.session_ticket.size()));
    if (session == nullptr) {
      return false;
    }
    if (!SSL_set_session(ssl_, session)) {
      SSL_SESSION_free(session);
      return false;
    }
    if (config_.enable_early_data &&
        SSL_SESSION_early_data_capable(session)) {
      // 0-RTT トランスポートパラメータを記憶していない場合は試行しない
      // (RFC 9000 Section 7.4.1 の MUST。将来改訂される可能性がある)
      if (!config_.early_transport_params.empty()) {
        SSL_set_early_data_enabled(ssl_, 1);
        early_data_attempted_ = true;
      }
    }
    SSL_SESSION_free(session);
  }

  return true;
}

bool QuicConnection::initialize_client(const std::string& local_host,
                                       uint16_t local_port,
                                       const std::string& remote_host,
                                       uint16_t remote_port) {
  // ngtcp2_conn_client_new より先に path を実アドレスで埋める
  if (!update_path_addresses(local_host, local_port, remote_host,
                             remote_port)) {
    return false;
  }

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
  callbacks.path_validation = path_validation_cb;
  callbacks.tls_early_data_rejected = tls_early_data_rejected_cb;

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

  // パスの設定
  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

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

  if (!setup_client_session()) {
    return false;
  }

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  // 0-RTT トランスポートパラメータを import
  if (early_data_attempted_ && !config_.early_transport_params.empty()) {
    int tp_rv = ngtcp2_conn_decode_and_set_0rtt_transport_params(
        conn_, config_.early_transport_params.data(),
        config_.early_transport_params.size());
    if (tp_rv != 0) {
      early_data_attempted_ = false;
    }
  }

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
  callbacks.path_validation = path_validation_cb;
  callbacks.tls_early_data_rejected = tls_early_data_rejected_cb;

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

  // パスの設定
  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

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

  if (!setup_server_early_data()) {
    return false;
  }

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  return true;
}

bool QuicConnection::initialize_server_from_packet(
    const std::vector<uint8_t>& initial_packet,
    const std::string& local_host,
    uint16_t local_port,
    const std::string& remote_host,
    uint16_t remote_port) {
  // ngtcp2_conn_server_new より先に path を実アドレスで埋める。
  // ゼロ path で生成したあと別アドレスで read_pkt すると DROP_CONN になる。
  if (!update_path_addresses(local_host, local_port, remote_host,
                             remote_port)) {
    return false;
  }

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
  callbacks.path_validation = path_validation_cb;
  callbacks.tls_early_data_rejected = tls_early_data_rejected_cb;

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

  // パスの設定
  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

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

  if (!setup_server_early_data()) {
    return false;
  }

  // TLS native handle を設定 (BoringSSL は SSL* を直接渡す)
  ngtcp2_conn_set_tls_native_handle(conn_, ssl_);

  return true;
}

void QuicConnection::fill_ngtcp2_path(ngtcp2_path* path) const {
  path->local.addr =
      const_cast<sockaddr*>(reinterpret_cast<const sockaddr*>(&local_addr_));
  path->local.addrlen = local_addrlen_;
  path->remote.addr =
      const_cast<sockaddr*>(reinterpret_cast<const sockaddr*>(&remote_addr_));
  path->remote.addrlen = remote_addrlen_;
  path->user_data = nullptr;
}

bool QuicConnection::update_path_addresses(const std::string& local_host,
                                           uint16_t local_port,
                                           const std::string& remote_host,
                                           uint16_t remote_port) {
  sockaddr_storage local{};
  sockaddr_storage remote{};
  socklen_t local_len = 0;
  socklen_t remote_len = 0;
  if (!fill_sockaddr(&local, &local_len, local_host, local_port)) {
    return false;
  }
  if (!fill_sockaddr(&remote, &remote_len, remote_host, remote_port)) {
    return false;
  }
  local_addr_ = local;
  local_addrlen_ = local_len;
  remote_addr_ = remote;
  remote_addrlen_ = remote_len;
  return true;
}

QuicPacket QuicConnection::make_packet(const uint8_t* data,
                                       size_t len,
                                       const ngtcp2_path& path) const {
  QuicPacket packet;
  packet.data.assign(data, data + len);
  sockaddr_to_host_port(path.local.addr, path.local.addrlen, &packet.local_host,
                        &packet.local_port);
  sockaddr_to_host_port(path.remote.addr, path.remote.addrlen,
                        &packet.remote_host, &packet.remote_port);
  return packet;
}

size_t QuicConnection::receive(const std::vector<uint8_t>& data,
                               const std::string& local_host,
                               uint16_t local_port,
                               const std::string& remote_host,
                               uint16_t remote_port) {
  if (!conn_ || closed_) {
    return 0;
  }

  if (!update_path_addresses(local_host, local_port, remote_host,
                             remote_port)) {
    return 0;
  }

  // OpenSSL エラーキューをクリア
  ERR_clear_error();

  timestamp_ns_ = get_timestamp_ns();

  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

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
        // Retry 送出は RFC 9000 Section 8.1.2 が許可する (can) アドレス検証の
        // 応答だが、本ライブラリのサーバーには送出手段が無く継続不能なため、
        // 他の終了系エラーと同じく closed_ を立てる。
        closed_ = true;
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

std::optional<QuicPacket> QuicConnection::send() {
  // close() が生成した CONNECTION_CLOSE を 1 回だけ返す。closed_ が立って
  // いても未配送の CONNECTION_CLOSE があれば優先して返し、返した後は
  // 従来どおり nullopt を返す (Sans-IO 設計と整合)。
  if (pending_close_packet_) {
    auto packet = std::move(pending_close_packet_);
    pending_close_packet_ = std::nullopt;
    return packet;
  }

  if (!conn_ || closed_) {
    return std::nullopt;
  }

  // OpenSSL エラーキューをクリア
  ERR_clear_error();

  timestamp_ns_ = get_timestamp_ns();

  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

  ngtcp2_pkt_info pi{};
  ngtcp2_ssize nwrite = 0;

  // MORE フラグを使用してパケットを構築したかどうか。ngtcp2 の契約では
  // MORE 使用後は writev_stream / writev_datagram を呼び続けて正の値 (確定
  // パケット) か 0 が返るまで回し、それ以外の ngtcp2 API を呼んではならない
  // (ngtcp2.h の NGTCP2_WRITE_STREAM_FLAG_MORE の記述)。パケットの確定方法の
  // 判定に使う。
  bool more_used = false;

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
        more_used = true;
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
              // パケットに空きがあるため同じ呼び出し内で続きを書く
              continue;
            }
            // 進捗がない場合はこの呼び出しで諦めて次へ
            break;
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
        return make_packet(send_buffer_.data(),
                           static_cast<size_t>(nwrite), path);
      }

      // nwrite == 0: パケットを書けなかった (cwnd 枯渇など)。
      // ループを続けると無限ループになるため、ACK 待ちとして次回に回す
      break;
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
          more_used = true;
          // パケットに空きがあるため同じ呼び出し内で続きを書く
          continue;
        }
        // 進捗がない場合はこの呼び出しで諦めて次へ
        break;
      }
      break;
    }

    if (accepted) {
      datagram_queue_.pop_front();
      more_used = true;
    }

    if (nwrite > 0) {
      return make_packet(send_buffer_.data(), static_cast<size_t>(nwrite),
                         path);
    }

    // nwrite == 0: パケットを書けなかった。ループを続けると無限ループに
    // なるため、次回の呼び出しに回す
    break;
  }

  // MORE フラグを使用した場合は、パケットを確定する。ngtcp2 の契約では
  // MORE 使用後は writev_stream / writev_datagram を呼び続けて正の値か 0 が
  // 返るまで回し、それ以外の ngtcp2 API を呼んではならない。ストリームデータが
  // 無くなった場合は stream_id=-1 で確定する (ngtcp2.h の
  // NGTCP2_WRITE_STREAM_FLAG_MORE の記述)
  if (more_used) {
    ngtcp2_ssize ndatalen = 0;
    nwrite = ngtcp2_conn_writev_stream(conn_, &path, &pi, send_buffer_.data(),
                                       send_buffer_.size(), &ndatalen, 0, -1,
                                       nullptr, 0, timestamp_ns_);
    if (nwrite > 0) {
      return make_packet(send_buffer_.data(), static_cast<size_t>(nwrite),
                         path);
    }
  }

  // MORE 未使用の場合のみ通常のパケット (ACK など) を生成する
  nwrite = ngtcp2_conn_write_pkt(conn_, &path, &pi, send_buffer_.data(),
                                 send_buffer_.size(), timestamp_ns_);
  if (nwrite > 0) {
    return make_packet(send_buffer_.data(), static_cast<size_t>(nwrite), path);
  }

  return std::nullopt;
}

bool QuicConnection::initiate_migration(const std::string& local_host,
                                        uint16_t local_port,
                                        const std::string& remote_host,
                                        uint16_t remote_port) {
  if (!conn_ || closed_ || is_server_) {
    return false;
  }

  sockaddr_storage local{};
  sockaddr_storage remote{};
  socklen_t local_len = 0;
  socklen_t remote_len = 0;
  if (!fill_sockaddr(&local, &local_len, local_host, local_port)) {
    return false;
  }
  if (!fill_sockaddr(&remote, &remote_len, remote_host, remote_port)) {
    return false;
  }

  ngtcp2_path path{};
  path.local.addr = reinterpret_cast<sockaddr*>(&local);
  path.local.addrlen = local_len;
  path.remote.addr = reinterpret_cast<sockaddr*>(&remote);
  path.remote.addrlen = remote_len;
  path.user_data = nullptr;

  timestamp_ns_ = get_timestamp_ns();
  int rv = ngtcp2_conn_initiate_migration(conn_, &path, timestamp_ns_);
  return rv == 0;
}

std::vector<uint8_t> QuicConnection::export_session_ticket() const {
  return last_session_ticket_;
}

std::vector<uint8_t> QuicConnection::export_0rtt_transport_params() const {
  if (!conn_ || !handshake_completed_) {
    return {};
  }

  std::vector<uint8_t> buffer(256);
  ngtcp2_ssize nwrite = ngtcp2_conn_encode_0rtt_transport_params2(
      conn_, buffer.data(), buffer.size());
  if (nwrite < 0) {
    buffer.resize(4096);
    nwrite = ngtcp2_conn_encode_0rtt_transport_params2(conn_, buffer.data(),
                                                       buffer.size());
  }
  if (nwrite < 0) {
    return {};
  }
  buffer.resize(static_cast<size_t>(nwrite));
  return buffer;
}

bool QuicConnection::is_early_data_accepted() const {
  return ssl_ != nullptr && SSL_early_data_accepted(ssl_) != 0;
}

bool QuicConnection::was_early_data_attempted() const {
  return early_data_attempted_;
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
  // 0-RTT を試行するクライアント接続 (early_data_attempted_) では、
  // ハンドシェイク完了前にストリームを開いて early data を送れるようにする。
  // 根拠は RFC 9001 Section 4.6.1 (0-RTT でのアプリケーションデータ送信。
  // 将来改訂される可能性がある)。
  // サーバー側は early_data_attempted_ が常に false のため挙動は変わらない。
  if (!conn_ || closed_ || (!handshake_completed_ && !early_data_attempted_)) {
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

  // 読み取り側と書き込み側の両方をシャットダウンする
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

void QuicConnection::stop_sending(int64_t stream_id, uint64_t error_code) {
  if (!conn_ || closed_) {
    return;
  }

  // STOP_SENDING フレームを送出する
  int rv = ngtcp2_conn_shutdown_stream_read(conn_, 0, stream_id, error_code);
  if (rv != 0 && rv != NGTCP2_ERR_STREAM_NOT_FOUND && rv < NGTCP2_ERR_FATAL) {
    closed_ = true;
    push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                "stop sending error: " + std::string(ngtcp2_strerror(rv))});
  }
}

void QuicConnection::reset_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_ || closed_) {
    return;
  }

  // RESET_STREAM フレームを送出する
  int rv = ngtcp2_conn_shutdown_stream_write(conn_, 0, stream_id, error_code);
  if (rv != 0 && rv != NGTCP2_ERR_STREAM_NOT_FOUND) {
    if (rv < NGTCP2_ERR_FATAL) {
      closed_ = true;
      push_event({QuicEventType::ConnectionClosed, -1, {}, false, 0,
                  "reset stream error: " + std::string(ngtcp2_strerror(rv))});
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

  ngtcp2_path path{};
  fill_ngtcp2_path(&path);

  ngtcp2_pkt_info pi{};
  ngtcp2_ccerr ccerr;
  ngtcp2_ccerr_set_application_error(
      &ccerr, error_code, reinterpret_cast<const uint8_t*>(reason.c_str()),
      reason.size());

  ngtcp2_ssize nwrite = ngtcp2_conn_write_connection_close(
      conn_, &path, &pi, send_buffer_.data(), send_buffer_.size(), &ccerr,
      timestamp_ns_);

  // CONNECTION_CLOSE が生成できた場合はパケットを保持し、send() が 1 回
  // だけ返すようにする。生成できない場合 (クライアント Initial 未送信の
  // NGTCP2_ERR_INVALID_STATE やサーバー Initial 未受信の送出量上限) は
  // パケット無しで終了する (state 未確立のエンドポイントは closing 状態に
  // 入らない (RFC 9000 Section 10.2.3))。
  //
  // ハンドシェイク途中 (Initial 交換済み) で呼んだ場合は Initial または
  // Handshake パケットで CONNECTION_CLOSE を書けるが、ngtcp2 が error_code
  // を APPLICATION_ERROR に置換し reason を落とす (RFC 9000 Section 10.2.3)。
  if (nwrite > 0) {
    pending_close_packet_ =
        make_packet(send_buffer_.data(), static_cast<size_t>(nwrite), path);
  }

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

std::optional<ngtcp2_conn_info> QuicConnection::get_conn_info() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  ngtcp2_conn_info info;
  ngtcp2_conn_get_conn_info2(conn_, &info);
  return info;
}

std::optional<uint64_t> QuicConnection::latest_rtt() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->latest_rtt;
}

std::optional<uint64_t> QuicConnection::min_rtt() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->min_rtt;
}

std::optional<uint64_t> QuicConnection::smoothed_rtt() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->smoothed_rtt;
}

std::optional<uint64_t> QuicConnection::rttvar() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->rttvar;
}

std::optional<uint64_t> QuicConnection::cwnd() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->cwnd;
}

std::optional<uint64_t> QuicConnection::ssthresh() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->ssthresh;
}

std::optional<uint64_t> QuicConnection::bytes_in_flight() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->bytes_in_flight;
}

std::optional<uint64_t> QuicConnection::pkt_sent() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->pkt_sent;
}

std::optional<uint64_t> QuicConnection::bytes_sent() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->bytes_sent;
}

std::optional<uint64_t> QuicConnection::pkt_recv() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->pkt_recv;
}

std::optional<uint64_t> QuicConnection::bytes_recv() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->bytes_recv;
}

std::optional<uint64_t> QuicConnection::pkt_lost() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->pkt_lost;
}

std::optional<uint64_t> QuicConnection::bytes_lost() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->bytes_lost;
}

std::optional<uint64_t> QuicConnection::ping_recv() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->ping_recv;
}

std::optional<uint64_t> QuicConnection::pkt_discarded() const {
  const auto info = get_conn_info();
  if (!info) {
    return std::nullopt;
  }
  return info->pkt_discarded;
}

std::optional<uint64_t> QuicConnection::pto() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return ngtcp2_conn_get_pto2(conn_);
}

std::optional<uint64_t> QuicConnection::cwnd_left() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return ngtcp2_conn_get_cwnd_left2(conn_);
}

std::optional<uint64_t> QuicConnection::max_data_left() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return ngtcp2_conn_get_max_data_left2(conn_);
}

std::optional<uint64_t> QuicConnection::max_stream_data_left(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return ngtcp2_conn_get_max_stream_data_left2(conn_, stream_id);
}

std::optional<uint64_t> QuicConnection::stream_loss_count(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return static_cast<uint64_t>(
      ngtcp2_conn_get_stream_loss_count2(conn_, stream_id));
}

std::optional<uint64_t> QuicConnection::send_quantum() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return static_cast<uint64_t>(ngtcp2_conn_get_send_quantum2(conn_));
}

std::optional<uint64_t> QuicConnection::path_max_tx_udp_payload_size() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return static_cast<uint64_t>(
      ngtcp2_conn_get_path_max_tx_udp_payload_size2(conn_));
}

std::optional<uint64_t> QuicConnection::error_code() const {
  if (!conn_) {
    return std::nullopt;
  }
  const auto* ccerr = ngtcp2_conn_get_ccerr2(conn_);
  if (ccerr->error_code == 0) {
    return std::nullopt;
  }
  return ccerr->error_code;
}

std::optional<std::string> QuicConnection::reason() const {
  if (!conn_) {
    return std::nullopt;
  }
  const auto* ccerr = ngtcp2_conn_get_ccerr2(conn_);
  if (ccerr->error_code == 0) {
    return std::nullopt;
  }
  if (ccerr->reason == nullptr) {
    return std::string();
  }
  return std::string(reinterpret_cast<const char*>(ccerr->reason),
                     ccerr->reasonlen);
}

int QuicConnection::tls_error() const {
  if (!conn_) {
    return 0;
  }
  return ngtcp2_conn_get_tls_error2(conn_);
}

int QuicConnection::tls_alert() const {
  if (!conn_) {
    return 0;
  }
  return ngtcp2_conn_get_tls_alert2(conn_);
}

const ngtcp2_transport_params* QuicConnection::get_remote_transport_params()
    const {
  if (!conn_) {
    return nullptr;
  }
  return ngtcp2_conn_get_remote_transport_params2(conn_);
}

const ngtcp2_transport_params* QuicConnection::get_local_transport_params()
    const {
  if (!conn_) {
    return nullptr;
  }
  return ngtcp2_conn_get_local_transport_params2(conn_);
}

std::optional<uint64_t> QuicConnection::remote_max_idle_timeout() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->max_idle_timeout;
}

std::optional<uint64_t> QuicConnection::remote_max_udp_payload_size() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->max_udp_payload_size;
}

std::optional<uint64_t> QuicConnection::remote_initial_max_data() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_data;
}

std::optional<uint64_t>
QuicConnection::remote_initial_max_stream_data_bidi_local() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_stream_data_bidi_local;
}

std::optional<uint64_t>
QuicConnection::remote_initial_max_stream_data_bidi_remote() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_stream_data_bidi_remote;
}

std::optional<uint64_t> QuicConnection::remote_initial_max_stream_data_uni()
    const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_stream_data_uni;
}

std::optional<uint64_t> QuicConnection::remote_initial_max_streams_bidi()
    const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_streams_bidi;
}

std::optional<uint64_t> QuicConnection::remote_initial_max_streams_uni() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->initial_max_streams_uni;
}

std::optional<uint64_t> QuicConnection::remote_max_datagram_frame_size() const {
  const auto* params = get_remote_transport_params();
  if (!params) {
    return std::nullopt;
  }
  return params->max_datagram_frame_size;
}

uint64_t QuicConnection::local_max_idle_timeout() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->max_idle_timeout;
}

uint64_t QuicConnection::local_max_udp_payload_size() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->max_udp_payload_size;
}

uint64_t QuicConnection::local_initial_max_data() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_data;
}

uint64_t QuicConnection::local_initial_max_stream_data_bidi_local() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_stream_data_bidi_local;
}

uint64_t QuicConnection::local_initial_max_stream_data_bidi_remote() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_stream_data_bidi_remote;
}

uint64_t QuicConnection::local_initial_max_stream_data_uni() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_stream_data_uni;
}

uint64_t QuicConnection::local_initial_max_streams_bidi() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_streams_bidi;
}

uint64_t QuicConnection::local_initial_max_streams_uni() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->initial_max_streams_uni;
}

uint64_t QuicConnection::local_max_datagram_frame_size() const {
  const auto* params = get_local_transport_params();
  if (!params) {
    return 0;
  }
  return params->max_datagram_frame_size;
}

uint32_t QuicConnection::negotiated_version() const {
  if (!conn_) {
    return 0;
  }
  return ngtcp2_conn_get_negotiated_version2(conn_);
}

uint32_t QuicConnection::client_chosen_version() const {
  if (!conn_) {
    return 0;
  }
  return ngtcp2_conn_get_client_chosen_version2(conn_);
}

bool QuicConnection::in_closing_period() const {
  if (!conn_) {
    return false;
  }
  return ngtcp2_conn_in_closing_period2(conn_) != 0;
}

bool QuicConnection::in_draining_period() const {
  if (!conn_) {
    return false;
  }
  return ngtcp2_conn_in_draining_period2(conn_) != 0;
}

std::vector<std::vector<uint8_t>> QuicConnection::scid() const {
  std::vector<std::vector<uint8_t>> result;
  if (!conn_) {
    return result;
  }
  const size_t n = ngtcp2_conn_get_scid2(conn_, nullptr);
  std::vector<ngtcp2_cid> cids(n);
  ngtcp2_conn_get_scid2(conn_, cids.data());
  result.reserve(n);
  for (const auto& cid : cids) {
    result.emplace_back(cid.data, cid.data + cid.datalen);
  }
  return result;
}

std::vector<std::vector<uint8_t>> QuicConnection::active_dcid() const {
  std::vector<std::vector<uint8_t>> result;
  if (!conn_) {
    return result;
  }
  const size_t n = ngtcp2_conn_get_active_dcid3(conn_, nullptr);
  std::vector<ngtcp2_cid_token2> dcids(n);
  ngtcp2_conn_get_active_dcid3(conn_, dcids.data());
  result.reserve(n);
  for (const auto& dcid : dcids) {
    result.emplace_back(dcid.cid.data, dcid.cid.data + dcid.cid.datalen);
  }
  return result;
}

void QuicConnection::push_event(QuicEvent event) {
  events_.push_back(std::move(event));
}

// ========== ngtcp2 / BoringSSL コールバック ==========

int QuicConnection::new_session_cb(SSL* ssl, SSL_SESSION* session) {
  auto* conn_ref =
      static_cast<ngtcp2_crypto_conn_ref*>(SSL_get_app_data(ssl));
  if (conn_ref == nullptr) {
    return 0;
  }
  auto* self = static_cast<QuicConnection*>(conn_ref->user_data);
  if (self == nullptr) {
    return 0;
  }

  // セッションを DER にシリアライズ
  int length = i2d_SSL_SESSION(session, nullptr);
  if (length <= 0) {
    return 0;
  }

  std::vector<uint8_t> der(static_cast<size_t>(length));
  uint8_t* pointer = der.data();
  if (i2d_SSL_SESSION(session, &pointer) <= 0) {
    return 0;
  }

  self->last_session_ticket_ = der;

  QuicEvent event;
  event.type = QuicEventType::SessionTicket;
  event.data = std::move(der);
  self->push_event(std::move(event));

  return 0;
}

ssl_verify_result_t QuicConnection::custom_verify_cb(SSL* ssl,
                                                     uint8_t* out_alert) {
  (void)out_alert;
  auto* conn_ref =
      static_cast<ngtcp2_crypto_conn_ref*>(SSL_get_app_data(ssl));
  if (conn_ref == nullptr) {
    return ssl_verify_invalid;
  }
  auto* self = static_cast<QuicConnection*>(conn_ref->user_data);
  if (self == nullptr || !self->config_.verify_callback) {
    return ssl_verify_invalid;
  }

  const STACK_OF(CRYPTO_BUFFER)* chain = SSL_get0_peer_certificates(ssl);
  if (chain == nullptr) {
    return ssl_verify_invalid;
  }

  std::vector<std::vector<uint8_t>> certificates;
  const size_t count = sk_CRYPTO_BUFFER_num(chain);
  certificates.reserve(count);
  for (size_t i = 0; i < count; ++i) {
    const CRYPTO_BUFFER* buffer = sk_CRYPTO_BUFFER_value(chain, i);
    const uint8_t* data = CRYPTO_BUFFER_data(buffer);
    size_t length = CRYPTO_BUFFER_len(buffer);
    certificates.emplace_back(data, data + length);
  }

  if (self->config_.verify_callback(certificates)) {
    return ssl_verify_ok;
  }
  return ssl_verify_invalid;
}

int QuicConnection::client_initial_cb(ngtcp2_conn* conn, void* user_data) {
  auto* self = static_cast<QuicConnection*>(user_data);
  (void)self;
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

  // 早期データ拒否のフォールバック検出 (RFC 9001 Section 4.6.2。
  // 将来改訂される可能性がある)。BoringSSL 統合層が
  // SSL_ERROR_EARLY_DATA_REJECTED で通知するため通常は呼ばれないが、
  // ngtcp2 側のフラグガードにより二重実行しても安全である。
  if (self->early_data_attempted_ && self->ssl_ != nullptr &&
      !SSL_early_data_accepted(self->ssl_)) {
    ngtcp2_conn_tls_early_data_rejected(conn);
  }

  QuicEvent event;
  event.type = QuicEventType::HandshakeCompleted;
  self->push_event(std::move(event));

  return 0;
}

int QuicConnection::path_validation_cb(ngtcp2_conn* conn,
                                       uint32_t flags,
                                       const ngtcp2_path* path,
                                       const ngtcp2_path* fallback_path,
                                       ngtcp2_path_validation_result res,
                                       void* user_data) {
  (void)conn;
  (void)flags;
  (void)fallback_path;
  auto* self = static_cast<QuicConnection*>(user_data);

  QuicEvent event;
  event.reason = format_path_reason(path);
  if (res == NGTCP2_PATH_VALIDATION_RESULT_SUCCESS) {
    event.type = QuicEventType::PathValidated;
    // 検証成功パスを現在パスとして保持する
    if (path->local.addrlen <= sizeof(self->local_addr_) &&
        path->remote.addrlen <= sizeof(self->remote_addr_)) {
      std::memcpy(&self->local_addr_, path->local.addr, path->local.addrlen);
      self->local_addrlen_ = path->local.addrlen;
      std::memcpy(&self->remote_addr_, path->remote.addr, path->remote.addrlen);
      self->remote_addrlen_ = path->remote.addrlen;
    }
  } else {
    event.type = QuicEventType::PathValidationFailed;
  }
  self->push_event(std::move(event));
  return 0;
}

int QuicConnection::tls_early_data_rejected_cb(ngtcp2_conn* conn,
                                               void* user_data) {
  (void)conn;
  auto* self = static_cast<QuicConnection*>(user_data);
  if (!self->early_data_rejected_event_pushed_) {
    self->early_data_rejected_event_pushed_ = true;
    QuicEvent event;
    event.type = QuicEventType::EarlyDataRejected;
    self->push_event(std::move(event));
  }
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
      .def_rw("verify_peer", &QuicConfig::verify_peer,
              "クライアントのピア証明書検証を行うか")
      .def_rw("ca_file", &QuicConfig::ca_file, "CA 証明書ファイルパス")
      .def_prop_rw(
          "verify_callback",
          [](QuicConfig& config) -> nb::object {
            if (!config.verify_callback) {
              return nb::none();
            }
            return nb::cpp_function(config.verify_callback);
          },
          [](QuicConfig& config, nb::object obj) {
            if (obj.is_none()) {
              config.verify_callback = nullptr;
            } else {
              config.verify_callback = nb::cast<std::function<bool(
                  const std::vector<std::vector<uint8_t>>&)>>(obj);
            }
          },
          "ピア証明書検証コールバック (list[bytes] -> bool) または None")
      .def_rw("enable_datagram", &QuicConfig::enable_datagram,
              "Datagram を有効にするか")
      .def_rw("max_datagram_frame_size", &QuicConfig::max_datagram_frame_size,
              "最大 Datagram フレームサイズ")
      .def_rw("enable_early_data", &QuicConfig::enable_early_data,
              "0-RTT early data を有効にするか")
      .def_prop_rw(
          "session_ticket",
          [](const QuicConfig& config) {
            return nb::bytes(
                reinterpret_cast<const char*>(config.session_ticket.data()),
                config.session_ticket.size());
          },
          [](QuicConfig& config, nb::bytes value) {
            config.session_ticket.assign(
                value.c_str(), value.c_str() + value.size());
          },
          "セッションチケット (DER bytes)")
      .def_prop_rw(
          "early_transport_params",
          [](const QuicConfig& config) {
            return nb::bytes(reinterpret_cast<const char*>(
                                 config.early_transport_params.data()),
                             config.early_transport_params.size());
          },
          [](QuicConfig& config, nb::bytes value) {
            config.early_transport_params.assign(
                value.c_str(), value.c_str() + value.size());
          },
          "0-RTT トランスポートパラメータ (bytes)");

  // QuicEventType
  nb::enum_<QuicEventType>(quic_m, "EventType", "QUIC イベント種別")
      .value("HANDSHAKE_COMPLETED", QuicEventType::HandshakeCompleted)
      .value("CONNECTION_CLOSED", QuicEventType::ConnectionClosed)
      .value("STREAM_DATA", QuicEventType::StreamData)
      .value("STREAM_OPENED", QuicEventType::StreamOpened)
      .value("STREAM_CLOSED", QuicEventType::StreamClosed)
      .value("STREAM_RESET", QuicEventType::StreamReset)
      .value("DATAGRAM", QuicEventType::DatagramReceived)
      .value("CONNECTION_ID_RETIRED", QuicEventType::ConnectionIdRetired)
      .value("SESSION_TICKET", QuicEventType::SessionTicket)
      .value("EARLY_DATA_REJECTED", QuicEventType::EarlyDataRejected)
      .value("PATH_VALIDATED", QuicEventType::PathValidated)
      .value("PATH_VALIDATION_FAILED", QuicEventType::PathValidationFailed);

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

  // QuicPacket
  nb::class_<QuicPacket>(quic_m, "Packet", "QUIC UDP パケット (パス情報付き)")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_prop_ro(
          "data",
          [](const QuicPacket& packet) {
            return nb::bytes(
                reinterpret_cast<const char*>(packet.data.data()),
                packet.data.size());
          },
          "パケットデータ")
      .def_ro("local_host", &QuicPacket::local_host, "ローカルホスト")
      .def_ro("local_port", &QuicPacket::local_port, "ローカルポート")
      .def_ro("remote_host", &QuicPacket::remote_host, "リモートホスト")
      .def_ro("remote_port", &QuicPacket::remote_port, "リモートポート");

  // QuicConnection
  nb::class_<QuicConnection>(quic_m, "Connection",
                             "QUIC コネクション (Sans-IO)")
      .def_static(
          "create_client",
          [](const QuicConfig& config,
             std::pair<std::string, uint16_t> local_addr,
             std::pair<std::string, uint16_t> remote_addr) {
            auto conn = QuicConnection::create_client(
                config, local_addr.first, local_addr.second, remote_addr.first,
                remote_addr.second);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create QUIC client connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::arg("local_addr"), nb::arg("remote_addr"),
          nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config, "
                  "local_addr: tuple[str, int], "
                  "remote_addr: tuple[str, int]) -> Connection"),
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
          [](const QuicConfig& config, nb::bytes initial_packet,
             std::pair<std::string, uint16_t> local_addr,
             std::pair<std::string, uint16_t> remote_addr) {
            auto conn = QuicConnection::accept(
                config,
                std::vector<uint8_t>(
                    initial_packet.c_str(),
                    initial_packet.c_str() + initial_packet.size()),
                local_addr.first, local_addr.second, remote_addr.first,
                remote_addr.second);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to accept QUIC connection from initial packet");
            }
            return conn.release();
          },
          nb::arg("config"), nb::arg("initial_packet"), nb::arg("local_addr"),
          nb::arg("remote_addr"), nb::rv_policy::take_ownership,
          nb::sig("def accept(config: Config, initial_packet: bytes, "
                  "local_addr: tuple[str, int], "
                  "remote_addr: tuple[str, int]) -> Connection"),
          "初期パケットからサーバー接続を作成")
      .def(
          "receive",
          [](QuicConnection& self, nb::bytes data,
             std::pair<std::string, uint16_t> local_addr,
             std::pair<std::string, uint16_t> remote_addr) {
            return self.receive(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                local_addr.first, local_addr.second, remote_addr.first,
                remote_addr.second);
          },
          nb::arg("data"), nb::arg("local_addr"), nb::arg("remote_addr"),
          nb::sig("def receive(self, data: bytes, local_addr: tuple[str, int], "
                  "remote_addr: tuple[str, int]) -> int"),
          "受信したデータを処理")
      .def(
          "send",
          [](QuicConnection& self) -> nb::object {
            auto result = self.send();
            if (result) {
              return nb::cast(std::move(*result));
            }
            return nb::none();
          },
          nb::sig("def send(self) -> Packet | None"),
          "送信すべきデータを取得")
      .def(
          "initiate_migration",
          [](QuicConnection& self,
             std::pair<std::string, uint16_t> local_addr,
             std::pair<std::string, uint16_t> remote_addr) {
            return self.initiate_migration(local_addr.first, local_addr.second,
                                           remote_addr.first,
                                           remote_addr.second);
          },
          nb::arg("local_addr"), nb::arg("remote_addr"),
          nb::sig("def initiate_migration(self, local_addr: tuple[str, int], "
                  "remote_addr: tuple[str, int]) -> bool"),
          "コネクションマイグレーションを開始する")
      .def(
          "export_session_ticket",
          [](const QuicConnection& self) {
            auto ticket = self.export_session_ticket();
            return nb::bytes(reinterpret_cast<const char*>(ticket.data()),
                             ticket.size());
          },
          nb::sig("def export_session_ticket(self) -> bytes"),
          "セッションチケット (DER) を取得")
      .def(
          "export_0rtt_transport_params",
          [](const QuicConnection& self) {
            auto params = self.export_0rtt_transport_params();
            return nb::bytes(reinterpret_cast<const char*>(params.data()),
                             params.size());
          },
          nb::sig("def export_0rtt_transport_params(self) -> bytes"),
          "0-RTT トランスポートパラメータを取得")
      .def("is_early_data_accepted", &QuicConnection::is_early_data_accepted,
           nb::sig("def is_early_data_accepted(self) -> bool"),
           "0-RTT early data が受理されたか")
      .def("was_early_data_attempted",
           &QuicConnection::was_early_data_attempted,
           nb::sig("def was_early_data_attempted(self) -> bool"),
           "0-RTT early data を試みたか")
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
           "ストリームを閉じる (RESET_STREAM + STOP_SENDING)")
      .def("stop_sending", &QuicConnection::stop_sending, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def stop_sending(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "STOP_SENDING を送出する")
      .def("reset_stream", &QuicConnection::reset_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "RESET_STREAM を送出する")
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
          nb::sig("def get_connection_id(self) -> bytes"), "接続 ID を取得")
      .def_prop_ro("latest_rtt", &QuicConnection::latest_rtt,
                   nb::sig("def latest_rtt(self) -> int | None"),
                   "最新の RTT (ナノ秒)")
      .def_prop_ro("min_rtt", &QuicConnection::min_rtt,
                   nb::sig("def min_rtt(self) -> int | None"),
                   "最小の RTT (ナノ秒)")
      .def_prop_ro("smoothed_rtt", &QuicConnection::smoothed_rtt,
                   nb::sig("def smoothed_rtt(self) -> int | None"),
                   "平滑化された RTT (ナノ秒)")
      .def_prop_ro("rttvar", &QuicConnection::rttvar,
                   nb::sig("def rttvar(self) -> int | None"),
                   "RTT の平均偏差 (ナノ秒)")
      .def_prop_ro("cwnd", &QuicConnection::cwnd,
                   nb::sig("def cwnd(self) -> int | None"),
                   "輻輳ウィンドウ (バイト)")
      .def_prop_ro("ssthresh", &QuicConnection::ssthresh,
                   nb::sig("def ssthresh(self) -> int | None"),
                   "スロー スタート閾値 (バイト)")
      .def_prop_ro("bytes_in_flight", &QuicConnection::bytes_in_flight,
                   nb::sig("def bytes_in_flight(self) -> int | None"),
                   "送信中で未 ACK のバイト数")
      .def_prop_ro("pkt_sent", &QuicConnection::pkt_sent,
                   nb::sig("def pkt_sent(self) -> int | None"),
                   "送信したパケット数")
      .def_prop_ro("bytes_sent", &QuicConnection::bytes_sent,
                   nb::sig("def bytes_sent(self) -> int | None"),
                   "送信したバイト数")
      .def_prop_ro("pkt_recv", &QuicConnection::pkt_recv,
                   nb::sig("def pkt_recv(self) -> int | None"),
                   "受信したパケット数 (破棄パケット除外)")
      .def_prop_ro("bytes_recv", &QuicConnection::bytes_recv,
                   nb::sig("def bytes_recv(self) -> int | None"),
                   "受信したバイト数 (破棄パケット除外)")
      .def_prop_ro("pkt_lost", &QuicConnection::pkt_lost,
                   nb::sig("def pkt_lost(self) -> int | None"),
                   "損失したパケット数 (PMTUD パケット除外)")
      .def_prop_ro("bytes_lost", &QuicConnection::bytes_lost,
                   nb::sig("def bytes_lost(self) -> int | None"),
                   "損失したバイト数 (PMTUD パケット除外)")
      .def_prop_ro("ping_recv", &QuicConnection::ping_recv,
                   nb::sig("def ping_recv(self) -> int | None"),
                   "受信した PING フレーム数")
      .def_prop_ro("pkt_discarded", &QuicConnection::pkt_discarded,
                   nb::sig("def pkt_discarded(self) -> int | None"),
                   "破棄したパケット数")
      .def_prop_ro("pto", &QuicConnection::pto,
                   nb::sig("def pto(self) -> int | None"),
                   "PTO (プローブタイムアウト) (ナノ秒)")
      .def_prop_ro("cwnd_left", &QuicConnection::cwnd_left,
                   nb::sig("def cwnd_left(self) -> int | None"),
                   "輻輳ウィンドウ残量 (バイト)")
      .def_prop_ro("max_data_left", &QuicConnection::max_data_left,
                   nb::sig("def max_data_left(self) -> int | None"),
                   "コネクション全体のフロー制御残量 (バイト)")
      .def("max_stream_data_left", &QuicConnection::max_stream_data_left,
           nb::arg("stream_id"),
           nb::sig(
               "def max_stream_data_left(self, stream_id: int) -> int | None"),
           "ストリームごとのフロー制御残量 (バイト)")
      .def("stream_loss_count", &QuicConnection::stream_loss_count,
           nb::arg("stream_id"),
           nb::sig("def stream_loss_count(self, stream_id: int) -> int | None"),
           "STREAM フレームを含む損失パケット数 (スプリアス損失を含む)")
      .def_prop_ro("send_quantum", &QuicConnection::send_quantum,
                   nb::sig("def send_quantum(self) -> int | None"),
                   "送信クォンタム (バイト)")
      .def_prop_ro(
          "path_max_tx_udp_payload_size",
          &QuicConnection::path_max_tx_udp_payload_size,
          nb::sig("def path_max_tx_udp_payload_size(self) -> int | None"),
          "現在パスの最大 UDP ペイロードサイズ (バイト)")
      .def_prop_ro("error_code", &QuicConnection::error_code,
                   nb::sig("def error_code(self) -> int | None"),
                   "コネクションエラーのコード (エラーが無い場合は None)")
      .def_prop_ro(
          "reason",
          [](const QuicConnection& self) -> nb::object {
            const auto reason = self.reason();
            if (!reason) {
              return nb::none();
            }
            // ピアが送る reason は UTF-8 とは限らない (RFC 9000 は SHOULD)。
            // 不正バイトで例外にしないため surrogateescape でデコードする。
            return nb::steal(PyUnicode_DecodeUTF8(
                reason->c_str(), static_cast<Py_ssize_t>(reason->size()),
                "surrogateescape"));
          },
          nb::sig("def reason(self) -> str | None"),
          "コネクションエラーの理由 (エラーが無い場合は None)")
      .def_prop_ro(
          "tls_error", &QuicConnection::tls_error,
          nb::sig("def tls_error(self) -> int"),
          "TLS 処理時に ngtcp2 が記録した内部エラーコード (無ければ 0)")
      .def_prop_ro("tls_alert", &QuicConnection::tls_alert,
                   nb::sig("def tls_alert(self) -> int"),
                   "TLS アラート (エラーが無い場合は 0)")
      .def_prop_ro("remote_max_idle_timeout",
                   &QuicConnection::remote_max_idle_timeout,
                   nb::sig("def remote_max_idle_timeout(self) -> int | None"),
                   "ピアのアイドルタイムアウト (ナノ秒)")
      .def_prop_ro(
          "remote_max_udp_payload_size",
          &QuicConnection::remote_max_udp_payload_size,
          nb::sig("def remote_max_udp_payload_size(self) -> int | None"),
          "ピアの最大 UDP ペイロードサイズ (バイト)")
      .def_prop_ro("remote_initial_max_data",
                   &QuicConnection::remote_initial_max_data,
                   nb::sig("def remote_initial_max_data(self) -> int | None"),
                   "ピアのコネクション全体のフロー制御上限")
      .def_prop_ro(
          "remote_initial_max_stream_data_bidi_local",
          &QuicConnection::remote_initial_max_stream_data_bidi_local,
          nb::sig("def remote_initial_max_stream_data_bidi_local(self) -> int "
                  "| None"),
          "ピアの双方向ストリーム (ローカル開始) のフロー制御上限")
      .def_prop_ro(
          "remote_initial_max_stream_data_bidi_remote",
          &QuicConnection::remote_initial_max_stream_data_bidi_remote,
          nb::sig("def remote_initial_max_stream_data_bidi_remote(self) -> "
                  "int | None"),
          "ピアの双方向ストリーム (リモート開始) のフロー制御上限")
      .def_prop_ro(
          "remote_initial_max_stream_data_uni",
          &QuicConnection::remote_initial_max_stream_data_uni,
          nb::sig("def remote_initial_max_stream_data_uni(self) -> int | None"),
          "ピアの単方向ストリームのフロー制御上限")
      .def_prop_ro(
          "remote_initial_max_streams_bidi",
          &QuicConnection::remote_initial_max_streams_bidi,
          nb::sig("def remote_initial_max_streams_bidi(self) -> int | None"),
          "ピアの双方向ストリーム並列数上限")
      .def_prop_ro(
          "remote_initial_max_streams_uni",
          &QuicConnection::remote_initial_max_streams_uni,
          nb::sig("def remote_initial_max_streams_uni(self) -> int | None"),
          "ピアの単方向ストリーム並列数上限")
      .def_prop_ro(
          "remote_max_datagram_frame_size",
          &QuicConnection::remote_max_datagram_frame_size,
          nb::sig("def remote_max_datagram_frame_size(self) -> int | None"),
          "ピアの Datagram フレームサイズ上限")
      .def_prop_ro("local_max_idle_timeout",
                   &QuicConnection::local_max_idle_timeout,
                   nb::sig("def local_max_idle_timeout(self) -> int"),
                   "ローカルのアイドルタイムアウト (ナノ秒)")
      .def_prop_ro("local_max_udp_payload_size",
                   &QuicConnection::local_max_udp_payload_size,
                   nb::sig("def local_max_udp_payload_size(self) -> int"),
                   "ローカルの最大 UDP ペイロードサイズ (バイト)")
      .def_prop_ro("local_initial_max_data",
                   &QuicConnection::local_initial_max_data,
                   nb::sig("def local_initial_max_data(self) -> int"),
                   "ローカルのコネクション全体のフロー制御上限")
      .def_prop_ro(
          "local_initial_max_stream_data_bidi_local",
          &QuicConnection::local_initial_max_stream_data_bidi_local,
          nb::sig("def local_initial_max_stream_data_bidi_local(self) -> int"),
          "ローカルの双方向ストリーム (ローカル開始) のフロー制御上限")
      .def_prop_ro(
          "local_initial_max_stream_data_bidi_remote",
          &QuicConnection::local_initial_max_stream_data_bidi_remote,
          nb::sig("def local_initial_max_stream_data_bidi_remote(self) -> int"),
          "ローカルの双方向ストリーム (リモート開始) のフロー制御上限")
      .def_prop_ro(
          "local_initial_max_stream_data_uni",
          &QuicConnection::local_initial_max_stream_data_uni,
          nb::sig("def local_initial_max_stream_data_uni(self) -> int"),
          "ローカルの単方向ストリームのフロー制御上限")
      .def_prop_ro("local_initial_max_streams_bidi",
                   &QuicConnection::local_initial_max_streams_bidi,
                   nb::sig("def local_initial_max_streams_bidi(self) -> int"),
                   "ローカルの双方向ストリーム並列数上限")
      .def_prop_ro("local_initial_max_streams_uni",
                   &QuicConnection::local_initial_max_streams_uni,
                   nb::sig("def local_initial_max_streams_uni(self) -> int"),
                   "ローカルの単方向ストリーム並列数上限")
      .def_prop_ro("local_max_datagram_frame_size",
                   &QuicConnection::local_max_datagram_frame_size,
                   nb::sig("def local_max_datagram_frame_size(self) -> int"),
                   "ローカルの Datagram フレームサイズ上限")
      .def_prop_ro("negotiated_version", &QuicConnection::negotiated_version,
                   nb::sig("def negotiated_version(self) -> int"),
                   "ネゴシエーションされた QUIC バージョン (未確定なら 0)")
      .def_prop_ro("client_chosen_version",
                   &QuicConnection::client_chosen_version,
                   nb::sig("def client_chosen_version(self) -> int"),
                   "クライアントが選択した QUIC バージョン")
      .def_prop_ro("in_closing_period", &QuicConnection::in_closing_period,
                   nb::sig("def in_closing_period(self) -> bool"),
                   "CLOSING 状態か")
      .def_prop_ro("in_draining_period", &QuicConnection::in_draining_period,
                   nb::sig("def in_draining_period(self) -> bool"),
                   "DRAINING 状態か")
      .def_prop_ro(
          "scid",
          [](const QuicConnection& self) {
            std::vector<nb::bytes> result;
            for (const auto& cid : self.scid()) {
              result.emplace_back(reinterpret_cast<const char*>(cid.data()),
                                  cid.size());
            }
            return result;
          },
          nb::sig("def scid(self) -> list[bytes]"),
          "送信元接続 ID (SCID) の一覧")
      .def_prop_ro(
          "active_dcid",
          [](const QuicConnection& self) {
            std::vector<nb::bytes> result;
            for (const auto& cid : self.active_dcid()) {
              result.emplace_back(reinterpret_cast<const char*>(cid.data()),
                                  cid.size());
            }
            return result;
          },
          nb::sig("def active_dcid(self) -> list[bytes]"),
          "アクティブな宛先接続 ID (DCID) の一覧 (ハンドシェイク完了前は空)");

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
