/**
 * HTTP/3 バインディング (nghttp3 ラッパー)
 *
 * Sans-IO スタイルの HTTP/3 実装
 */

#include "http3.h"

#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace http3 {

// ========== Http3Connection 実装 ==========

Http3Connection::Http3Connection(bool is_server, const Http3Config& config)
    : is_server_(is_server), config_(config) {}

Http3Connection::~Http3Connection() {
  if (conn_) {
    nghttp3_conn_del(conn_);
  }
}

Http3Connection::Http3Connection(Http3Connection&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      conn_(other.conn_),
      events_(std::move(other.events_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      pending_sends_(std::move(other.pending_sends_)),
      pending_headers_(std::move(other.pending_headers_)),
      control_stream_id_(other.control_stream_id_),
      qpack_encoder_stream_id_(other.qpack_encoder_stream_id_),
      qpack_decoder_stream_id_(other.qpack_decoder_stream_id_),
      closed_(other.closed_),
      shutdown_stream_ids_(std::move(other.shutdown_stream_ids_)),
      shutdown_commenced_(other.shutdown_commenced_),
      shutdown_notice_sent_(other.shutdown_notice_sent_),
      max_client_streams_bidi_(other.max_client_streams_bidi_) {
  other.conn_ = nullptr;
}

Http3Connection& Http3Connection::operator=(Http3Connection&& other) noexcept {
  if (this != &other) {
    if (conn_)
      nghttp3_conn_del(conn_);

    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    conn_ = other.conn_;
    events_ = std::move(other.events_);
    stream_buffers_ = std::move(other.stream_buffers_);
    pending_sends_ = std::move(other.pending_sends_);
    pending_headers_ = std::move(other.pending_headers_);
    control_stream_id_ = other.control_stream_id_;
    qpack_encoder_stream_id_ = other.qpack_encoder_stream_id_;
    qpack_decoder_stream_id_ = other.qpack_decoder_stream_id_;
    closed_ = other.closed_;
    shutdown_stream_ids_ = std::move(other.shutdown_stream_ids_);
    shutdown_commenced_ = other.shutdown_commenced_;
    shutdown_notice_sent_ = other.shutdown_notice_sent_;
    max_client_streams_bidi_ = other.max_client_streams_bidi_;

    other.conn_ = nullptr;
  }
  return *this;
}

std::unique_ptr<Http3Connection> Http3Connection::create_client(
    const Http3Config& config) {
  Http3Config client_config = config;
  client_config.is_server = false;
  auto conn = std::unique_ptr<Http3Connection>(
      new Http3Connection(false, client_config));
  if (!conn->initialize()) {
    return nullptr;
  }
  return conn;
}

std::unique_ptr<Http3Connection> Http3Connection::create_server(
    const Http3Config& config) {
  Http3Config server_config = config;
  server_config.is_server = true;
  auto conn = std::unique_ptr<Http3Connection>(
      new Http3Connection(true, server_config));
  if (!conn->initialize()) {
    return nullptr;
  }
  return conn;
}

bool Http3Connection::initialize() {
  nghttp3_callbacks callbacks{};
  callbacks.acked_stream_data = acked_stream_data_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.recv_data = recv_data_cb;
  callbacks.deferred_consume = deferred_consume_cb;
  callbacks.begin_headers = begin_headers_cb;
  callbacks.recv_header = recv_header_cb;
  callbacks.end_headers = end_headers_cb;
  callbacks.begin_trailers = begin_trailers_cb;
  callbacks.recv_trailer = recv_trailer_cb;
  callbacks.end_trailers = end_trailers_cb;
  callbacks.stop_sending = stop_sending_cb;
  callbacks.reset_stream = reset_stream_cb;
  callbacks.shutdown = shutdown_cb;
  // deprecated の recv_settings ではなく recv_settings2 を使う
  callbacks.recv_settings2 = recv_settings2_cb;

  nghttp3_settings settings;
  nghttp3_settings_default(&settings);
  settings.max_field_section_size = config_.max_field_section_size;
  settings.qpack_max_dtable_capacity = config_.qpack_max_dtable_capacity;
  settings.qpack_blocked_streams = config_.qpack_blocked_streams;

  if (config_.enable_webtransport) {
    settings.enable_connect_protocol = 1;
  }

  if (config_.enable_h3_datagram) {
    settings.h3_datagram = 1;
  }

  int rv;
  if (is_server_) {
    rv = nghttp3_conn_server_new(&conn_, &callbacks, &settings,
                                 nghttp3_mem_default(), this);
  } else {
    rv = nghttp3_conn_client_new(&conn_, &callbacks, &settings,
                                 nghttp3_mem_default(), this);
  }

  return rv == 0;
}

size_t Http3Connection::receive_stream_data(int64_t stream_id,
                                            const std::vector<uint8_t>& data,
                                            bool fin) {
  if (!conn_ || closed_) {
    return 0;
  }

  nghttp3_ssize rv = nghttp3_conn_read_stream2(conn_, stream_id, data.data(),
                                               data.size(), fin ? 1 : 0, 0);
  if (rv < 0) {
    // nghttp3 のプロトコルエラー時に closed_ を立てる。
    // Http2Connection::receive の同種経路と対称にし、高レベル層の
    // is_closed() チェックで run() を終了させる。
    closed_ = true;
    return 0;
  }

  return static_cast<size_t>(rv);
}

std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>>
Http3Connection::get_streams_to_send() {
  std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>> result;

  if (!conn_ || closed_) {
    return result;
  }

  // nghttp3 から書き込むストリームを取得
  for (;;) {
    nghttp3_vec vec[16];
    int64_t stream_id = -1;
    int fin = 0;

    nghttp3_ssize sveccnt =
        nghttp3_conn_writev_stream(conn_, &stream_id, &fin, vec, 16);
    if (sveccnt < 0) {
      // nghttp3 の送信側プロトコルエラー時に closed_ を立てる。
      // Http2Connection::send の同種経路と対称にする。
      closed_ = true;
      break;
    }

    if (stream_id < 0) {
      break;
    }

    // データを収集
    std::vector<uint8_t> data;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      data.insert(data.end(), vec[i].base, vec[i].base + vec[i].len);
    }

    if (!data.empty() || fin) {
      result.emplace_back(stream_id, std::move(data), fin != 0);
    }

    // 書き込みを完了としてマーク
    size_t total_len = 0;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      total_len += vec[i].len;
    }
    // 進捗がない場合は打ち切る (WOULDBLOCK 相当)
    if (total_len == 0 && fin == 0) {
      break;
    }
    if (total_len > 0) {
      nghttp3_conn_add_write_offset(conn_, stream_id, total_len);
    } else if (fin) {
      // FIN のみの場合も offset 0 を通知する
      nghttp3_conn_add_write_offset(conn_, stream_id, 0);
    }

    if (sveccnt == 0) {
      break;
    }
  }

  return result;
}

void Http3Connection::bind_control_stream(int64_t stream_id) {
  if (!conn_ || closed_) {
    return;
  }

  control_stream_id_ = stream_id;
  nghttp3_conn_bind_control_stream(conn_, stream_id);
}

void Http3Connection::bind_qpack_encoder_stream(int64_t stream_id) {
  if (!conn_ || closed_) {
    return;
  }

  qpack_encoder_stream_id_ = stream_id;
  // 両方のストリーム ID が有効な場合のみバインドする
  if (qpack_decoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

void Http3Connection::bind_qpack_decoder_stream(int64_t stream_id) {
  if (!conn_ || closed_) {
    return;
  }

  qpack_decoder_stream_id_ = stream_id;
  if (qpack_encoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

bool Http3Connection::submit_request(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_ || is_server_) {
    return false;
  }

  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }

  std::vector<nghttp3_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp3_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP3_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  // データリーダーを設定してボディデータを送信可能にする
  nghttp3_data_reader dr;
  dr.read_data = read_data_cb;

  int rv = nghttp3_conn_submit_request(conn_, stream_id, nva.data(), nva.size(),
                                       &dr, nullptr);
  if (rv != 0) {
    return false;
  }

  stream_buffers_[stream_id] = {};
  return true;
}

bool Http3Connection::submit_response(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  std::vector<nghttp3_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp3_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP3_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  // データリーダーを設定してボディデータを送信可能にする
  nghttp3_data_reader dr;
  dr.read_data = read_data_cb;

  int rv = nghttp3_conn_submit_response(conn_, stream_id, nva.data(),
                                        nva.size(), &dr);
  if (rv != 0) {
    return false;
  }

  stream_buffers_[stream_id] = {};
  return true;
}

void Http3Connection::send_data(int64_t stream_id,
                                const std::vector<uint8_t>& data,
                                bool fin) {
  if (!conn_ || closed_) {
    return;
  }

  // 書き込み側シャットダウン済みのストリームは no-op とする
  if (shutdown_stream_ids_.contains(stream_id)) {
    return;
  }

  StreamData sd;
  sd.data = data;
  sd.offset = 0;
  sd.fin = fin;
  stream_buffers_[stream_id].push_back(std::move(sd));
  nghttp3_conn_resume_stream(conn_, stream_id);
}

void Http3Connection::reset_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_ || closed_) {
    return;
  }

  // nghttp3 に読み取り停止を伝え、高レベル側で QUIC RESET_STREAM を送出する
  nghttp3_conn_shutdown_stream_read(conn_, stream_id);
  stream_buffers_.erase(stream_id);
  shutdown_stream_ids_.erase(stream_id);

  Http3Event event;
  event.type = Http3EventType::ResetStream;
  event.stream_id = stream_id;
  event.error_code = error_code;
  push_event(std::move(event));
}

void Http3Connection::close_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_ || closed_) {
    return;
  }

  // QUIC ストリームが閉じられたことを nghttp3 に伝える
  // 成功すると stream_close_cb が呼ばれ STREAM_END が積まれる
  // STREAM_NOT_FOUND は既に閉じ済みとして無視する
  (void)nghttp3_conn_close_stream(conn_, stream_id, error_code);
}

void Http3Connection::goaway(int64_t id) {
  if (!conn_ || closed_) {
    return;
  }

  // コントロールストリームがバインドされていない場合は何もしない
  // nghttp3 は tx.ctrl が設定されていることを assert する
  if (control_stream_id_ < 0) {
    return;
  }

  nghttp3_conn_shutdown(conn_);

  // 以降の submit_shutdown_notice は GOAWAY ID の単調減少に違反するため
  // ガードする (RFC 9114 5.2 節の MUST NOT)
  shutdown_commenced_ = true;
}

bool Http3Connection::submit_trailers(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_) {
    return false;
  }

  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }

  // 書き込み側シャットダウン済みのストリームには送信しない
  if (shutdown_stream_ids_.contains(stream_id)) {
    return false;
  }

  std::vector<nghttp3_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp3_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP3_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  // トレーラはフレームキューに積まれる。flush 済み (WRITE_END_STREAM) の
  // ストリームでは NGHTTP3_ERR_INVALID_STATE になる
  int rv =
      nghttp3_conn_submit_trailers(conn_, stream_id, nva.data(), nva.size());
  if (rv != 0) {
    return false;
  }

  // READ_DATA_BLOCKED を解除してトレーラの書き出しを促す
  // (本体送信処理が完了している場合はフラグが立っているため)
  nghttp3_conn_resume_stream(conn_, stream_id);
  return true;
}

bool Http3Connection::submit_info(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }

  std::vector<nghttp3_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp3_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP3_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  // 1xx レスポンスはフレームキューに積まれる。最終レスポンス
  // (submit_response) より前に呼ぶこと。クライアントは is_server_
  // ガードで拒否する (nghttp3 は conn->server を assert する)
  int rv = nghttp3_conn_submit_info(conn_, stream_id, nva.data(), nva.size());
  if (rv != 0) {
    return false;
  }

  return true;
}

bool Http3Connection::submit_shutdown_notice() {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  // 制御ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.ctrl が設定されていることを assert する
  if (control_stream_id_ < 0) {
    return false;
  }

  // goaway() 済みの場合は false を返す。shutdown notice の GOAWAY ID は
  // shutdown の GOAWAY ID より大きいため、単調減少に違反する
  // (RFC 9114 5.2 節の MUST NOT。Release ビルドでは assert が
  // 無効化されるため C++ 側でガードする)
  if (shutdown_commenced_) {
    return false;
  }

  // 送信済みの場合は false を返す。同一 GOAWAY ID の重複送信を避ける
  // (RFC 9114 5.2 節では許容されるが、ピアに無意味なフレームを送らない)
  if (shutdown_notice_sent_) {
    return false;
  }

  int rv = nghttp3_conn_submit_shutdown_notice(conn_);
  if (rv != 0) {
    return false;
  }

  shutdown_notice_sent_ = true;
  return true;
}

void Http3Connection::shutdown_stream_write(int64_t stream_id) {
  if (!conn_ || closed_) {
    return;
  }

  // nghttp3 に書き込み側シャットダウンを伝える
  // (SHUT_WR フラグを立てる。クライアント発双方向ストリームでは
  // スケジューラからも外す)
  nghttp3_conn_shutdown_stream_write(conn_, stream_id);
  shutdown_stream_ids_.insert(stream_id);
}

std::optional<Http3Event> Http3Connection::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  auto event = std::move(events_.front());
  events_.pop_front();
  return event;
}

std::vector<std::pair<std::string, bool>>
Http3Connection::get_required_streams() const {
  // HTTP/3 は単方向ストリームを 3 本必要とする
  // control (server -> client または client -> server)
  // qpack encoder
  // qpack decoder
  return {
      {"control", false},        // false = 単方向
      {"qpack_encoder", false},  // false = 単方向
      {"qpack_decoder", false},  // false = 単方向
  };
}

bool Http3Connection::is_closed() const {
  return closed_;
}

std::optional<int> Http3Connection::stream_writable(int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return nghttp3_conn_is_stream_writable2(conn_, stream_id);
}

std::optional<int> Http3Connection::stream_flushed(int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return nghttp3_conn_is_stream_flushed(conn_, stream_id);
}

std::optional<uint64_t> Http3Connection::frame_payload_left(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  // nghttp3 は assert で stream_id の範囲を検証する (Release ビルドでは
  // 無効化されるため C++ 側でガードする。 NGHTTP3_MAX_VARINT は非公開
  // マクロ )
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return 0;
  }
  return nghttp3_conn_get_frame_payload_left2(conn_, stream_id);
}

std::optional<bool> Http3Connection::drained() const {
  if (!conn_ || closed_ || !is_server_) {
    return std::nullopt;
  }
  // nghttp3 は assert でサーバーセッションを要求し、制御ストリームを
  // 無条件に参照する (Release ビルドでは assert が無効化されるため
  // C++ 側でガードする。 goaway() と同じガード条件)
  if (control_stream_id_ < 0) {
    return std::nullopt;
  }
  return nghttp3_conn_is_drained2(conn_) != 0;
}

std::optional<std::pair<uint32_t, bool>> Http3Connection::stream_priority(
    int64_t stream_id) const {
  if (!conn_ || closed_ || !is_server_) {
    return std::nullopt;
  }
  // nghttp3 は assert で stream_id の範囲を検証する (Release ビルドでは
  // 無効化されるため C++ 側でガードする。 NGHTTP3_MAX_VARINT は非公開
  // マクロ )
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return std::nullopt;
  }

  nghttp3_pri pri;
  int rv = nghttp3_conn_get_stream_priority2(conn_, &pri, stream_id);
  if (rv != 0) {
    return std::nullopt;
  }
  return std::make_pair(pri.urgency, pri.inc != 0);
}

void Http3Connection::set_max_client_streams_bidi(uint64_t max_streams) {
  if (!conn_ || closed_ || !is_server_) {
    return;
  }
  // 累積最大数は単調増加のみ許可される (nghttp3 は assert で検証するが
  // Release ビルドでは無効化されるため C++ 側で減算を防ぐ)
  if (max_streams < max_client_streams_bidi_) {
    return;
  }
  max_client_streams_bidi_ = max_streams;
  nghttp3_conn_set_max_client_streams_bidi(conn_, max_streams);
}

bool Http3Connection::client_stream_priority(int64_t stream_id,
                                             uint32_t urgency,
                                             bool incremental) {
  if (!conn_ || closed_ || is_server_) {
    return false;
  }

  // 制御ストリームがバインドされていない場合は false を返す
  // (nghttp3 は制御ストリーム未バインド時に conn->tx.ctrl を NULL
  // 参照するため。 goaway() と同様のガード)
  if (control_stream_id_ < 0) {
    return false;
  }

  // nghttp3 は assert で stream_id と urgency の範囲を検証する
  // (Release ビルドでは無効化されるため C++ 側でガードする。
  // NGHTTP3_MAX_VARINT は非公開マクロ )
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return false;
  }
  if (urgency > NGHTTP3_URGENCY_LOW) {
    return false;
  }

  // RFC 9218 の Dictionary キーは u / i のみのため、タプルを
  // シリアライズ済みの priority field value に変換して渡す
  std::string value = "u=" + std::to_string(urgency);
  if (incremental) {
    value += ", i";
  }

  int rv = nghttp3_conn_set_client_stream_priority(
      conn_, stream_id, reinterpret_cast<const uint8_t*>(value.data()),
      value.size());
  return rv == 0;
}

bool Http3Connection::server_stream_priority(int64_t stream_id,
                                             uint32_t urgency,
                                             bool incremental) {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  // nghttp3 は assert で stream_id と urgency の範囲を検証する
  // (Release ビルドでは無効化されるため C++ 側でガードする。
  // NGHTTP3_MAX_VARINT は非公開マクロ )
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return false;
  }
  if (urgency > NGHTTP3_URGENCY_LOW) {
    return false;
  }

  nghttp3_pri pri = {urgency, static_cast<uint8_t>(incremental ? 1 : 0)};
  int rv = nghttp3_conn_set_server_stream_priority(conn_, stream_id, &pri);
  return rv == 0;
}

void Http3Connection::block_stream(int64_t stream_id) {
  if (!conn_ || closed_) {
    return;
  }
  nghttp3_conn_block_stream(conn_, stream_id);
}

bool Http3Connection::unblock_stream(int64_t stream_id) {
  if (!conn_ || closed_) {
    return false;
  }
  return nghttp3_conn_unblock_stream(conn_, stream_id) == 0;
}

void Http3Connection::max_concurrent_streams(size_t n) {
  if (!conn_ || closed_) {
    return;
  }
  nghttp3_conn_set_max_concurrent_streams(conn_, n);
}

void Http3Connection::push_event(Http3Event event) {
  events_.push_back(std::move(event));
}

// ========== nghttp3 コールバック ==========

int Http3Connection::acked_stream_data_cb(nghttp3_conn* conn,
                                          int64_t stream_id,
                                          uint64_t datalen,
                                          void* conn_user_data,
                                          void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  // 送信済みデータを削除
  auto it = self->stream_buffers_.find(stream_id);
  if (it != self->stream_buffers_.end()) {
    uint64_t remaining = datalen;
    while (remaining > 0 && !it->second.empty()) {
      auto& buffer = it->second.front();
      if (buffer.offset >= remaining) {
        // このバッファは全て ack された
        it->second.pop_front();
        remaining = 0;
      } else {
        remaining -= buffer.offset;
        it->second.pop_front();
      }
    }
    if (it->second.empty()) {
      self->stream_buffers_.erase(it);
    }
  }

  return 0;
}

int Http3Connection::stream_close_cb(nghttp3_conn* conn,
                                     int64_t stream_id,
                                     uint64_t app_error_code,
                                     void* conn_user_data,
                                     void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  Http3Event event;
  event.type = Http3EventType::StreamEnd;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));

  self->stream_buffers_.erase(stream_id);
  self->shutdown_stream_ids_.erase(stream_id);
  return 0;
}

int Http3Connection::recv_data_cb(nghttp3_conn* conn,
                                  int64_t stream_id,
                                  const uint8_t* data,
                                  size_t datalen,
                                  void* conn_user_data,
                                  void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  Http3Event event;
  event.type = Http3EventType::Data;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + datalen);
  self->push_event(std::move(event));

  return 0;
}

int Http3Connection::deferred_consume_cb(nghttp3_conn* conn,
                                         int64_t stream_id,
                                         size_t consumed,
                                         void* conn_user_data,
                                         void* stream_user_data) {
  return 0;
}

int Http3Connection::begin_headers_cb(nghttp3_conn* conn,
                                      int64_t stream_id,
                                      void* conn_user_data,
                                      void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);
  self->pending_headers_[stream_id] = {};
  return 0;
}

int Http3Connection::recv_header_cb(nghttp3_conn* conn,
                                    int64_t stream_id,
                                    int32_t token,
                                    nghttp3_rcbuf* name,
                                    nghttp3_rcbuf* value,
                                    uint8_t flags,
                                    void* conn_user_data,
                                    void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  auto name_vec = nghttp3_rcbuf_get_buf(name);
  auto value_vec = nghttp3_rcbuf_get_buf(value);

  std::string name_str(reinterpret_cast<const char*>(name_vec.base),
                       name_vec.len);
  std::string value_str(reinterpret_cast<const char*>(value_vec.base),
                        value_vec.len);

  self->pending_headers_[stream_id].emplace_back(std::move(name_str),
                                                 std::move(value_str));
  return 0;
}

int Http3Connection::end_headers_cb(nghttp3_conn* conn,
                                    int64_t stream_id,
                                    int fin,
                                    void* conn_user_data,
                                    void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  auto it = self->pending_headers_.find(stream_id);
  if (it != self->pending_headers_.end()) {
    Http3Event event;
    event.type = Http3EventType::Headers;
    event.stream_id = stream_id;
    event.headers = std::move(it->second);
    self->push_event(std::move(event));
    self->pending_headers_.erase(it);
  }

  if (fin) {
    Http3Event event;
    event.type = Http3EventType::StreamEnd;
    event.stream_id = stream_id;
    self->push_event(std::move(event));
  }

  return 0;
}

int Http3Connection::begin_trailers_cb(nghttp3_conn* conn,
                                       int64_t stream_id,
                                       void* conn_user_data,
                                       void* stream_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);
  self->pending_headers_[stream_id] = {};
  return 0;
}

int Http3Connection::recv_trailer_cb(nghttp3_conn* conn,
                                     int64_t stream_id,
                                     int32_t token,
                                     nghttp3_rcbuf* name,
                                     nghttp3_rcbuf* value,
                                     uint8_t flags,
                                     void* conn_user_data,
                                     void* stream_user_data) {
  return recv_header_cb(conn, stream_id, token, name, value, flags,
                        conn_user_data, stream_user_data);
}

int Http3Connection::end_trailers_cb(nghttp3_conn* conn,
                                     int64_t stream_id,
                                     int fin,
                                     void* conn_user_data,
                                     void* stream_user_data) {
  return end_headers_cb(conn, stream_id, fin, conn_user_data, stream_user_data);
}

int Http3Connection::stop_sending_cb(nghttp3_conn* conn,
                                     int64_t stream_id,
                                     uint64_t app_error_code,
                                     void* conn_user_data,
                                     void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  // nghttp3 が QUIC STOP_SENDING の送出を要求している
  Http3Event event;
  event.type = Http3EventType::StopSending;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));

  return 0;
}

int Http3Connection::reset_stream_cb(nghttp3_conn* conn,
                                     int64_t stream_id,
                                     uint64_t app_error_code,
                                     void* conn_user_data,
                                     void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  // nghttp3 が QUIC RESET_STREAM の送出を要求している
  Http3Event event;
  event.type = Http3EventType::ResetStream;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));

  self->stream_buffers_.erase(stream_id);
  self->shutdown_stream_ids_.erase(stream_id);
  return 0;
}

int Http3Connection::shutdown_cb(nghttp3_conn* conn,
                                 int64_t id,
                                 void* conn_user_data) {
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  Http3Event event;
  event.type = Http3EventType::GoAway;
  event.push_id = id;
  self->push_event(std::move(event));

  return 0;
}

int Http3Connection::recv_settings2_cb(nghttp3_conn* conn,
                                       const nghttp3_proto_settings* settings,
                                       void* conn_user_data) {
  // 素の HTTP/3 では設定を受け取った時点で追加処理は不要
  (void)conn;
  (void)settings;
  (void)conn_user_data;
  return 0;
}

nghttp3_ssize Http3Connection::read_data_cb(nghttp3_conn* conn,
                                            int64_t stream_id,
                                            nghttp3_vec* vec,
                                            size_t veccnt,
                                            uint32_t* pflags,
                                            void* conn_user_data,
                                            void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http3Connection*>(conn_user_data);

  auto it = self->stream_buffers_.find(stream_id);
  if (it == self->stream_buffers_.end() || it->second.empty()) {
    // データがない場合はブロック
    return NGHTTP3_ERR_WOULDBLOCK;
  }

  if (veccnt == 0) {
    return 0;
  }

  auto& buffers = it->second;

  // 送信済みで FIN なしのバッファはスキップして次へ進む
  // (ここで pop_front すると ALIEN 参照中のバッファが free され、
  //  ダングリングポインタになるため、削除は acked_stream_data_cb に任せる)
  for (auto itb = buffers.begin(); itb != buffers.end(); ++itb) {
    auto& buffer = *itb;
    size_t remaining = buffer.data.size() - buffer.offset;
    if (remaining == 0) {
      if (buffer.fin) {
        *pflags |= NGHTTP3_DATA_FLAG_EOF;
        return 0;
      }
      continue;
    }

    // データを返す（オフセットから開始）
    vec[0].base = const_cast<uint8_t*>(buffer.data.data() + buffer.offset);
    vec[0].len = remaining;

    // オフセットを更新（データはまだ削除しない - acked_stream_data_cb で削除）
    buffer.offset = buffer.data.size();

    if (buffer.fin && std::next(itb) == buffers.end()) {
      *pflags |= NGHTTP3_DATA_FLAG_EOF;
    }

    return 1;
  }

  return NGHTTP3_ERR_WOULDBLOCK;
}

// ========== Python バインディング ==========

void bind_http3(nb::module_& m) {
  auto http3_m = m.def_submodule("http3", "HTTP/3 protocol (nghttp3)");

  // Http3Config
  nb::class_<Http3Config>(http3_m, "Config", "HTTP/3 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("max_field_section_size", &Http3Config::max_field_section_size,
              "最大フィールドセクションサイズ")
      .def_rw("qpack_max_dtable_capacity",
              &Http3Config::qpack_max_dtable_capacity,
              "QPACK 動的テーブル最大容量")
      .def_rw("qpack_blocked_streams", &Http3Config::qpack_blocked_streams,
              "QPACK ブロックされたストリーム数")
      .def_rw("enable_webtransport", &Http3Config::enable_webtransport,
              "WebTransport 有効化")
      .def_rw("enable_h3_datagram", &Http3Config::enable_h3_datagram,
              "HTTP/3 Datagram 有効化")
      .def_rw("is_server", &Http3Config::is_server, "サーバーモード");

  // Http3EventType
  nb::enum_<Http3EventType>(http3_m, "EventType", "HTTP/3 イベント種別")
      .value("HEADERS", Http3EventType::Headers)
      .value("DATA", Http3EventType::Data)
      .value("STREAM_END", Http3EventType::StreamEnd)
      .value("PUSH_PROMISE", Http3EventType::PushPromise)
      .value("GO_AWAY", Http3EventType::GoAway)
      .value("RESET", Http3EventType::Reset)
      .value("RESET_STREAM", Http3EventType::ResetStream)
      .value("STOP_SENDING", Http3EventType::StopSending)
      .value("WEBTRANSPORT_SESSION_READY",
             Http3EventType::WebTransportSessionReady)
      .value("WEBTRANSPORT_STREAM_DATA", Http3EventType::WebTransportStreamData)
      .value("WEBTRANSPORT_DATAGRAM", Http3EventType::WebTransportDatagram);

  // Http3Event
  nb::class_<Http3Event>(http3_m, "Event", "HTTP/3 イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &Http3Event::type, "イベント種別")
      .def_ro("stream_id", &Http3Event::stream_id, "ストリーム ID")
      .def_ro("headers", &Http3Event::headers, "ヘッダー")
      .def_prop_ro(
          "data",
          [](const Http3Event& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "データ")
      .def_ro("error_code", &Http3Event::error_code, "エラーコード")
      .def_ro("push_id", &Http3Event::push_id, "Push ID");

  // Http3Connection
  nb::class_<Http3Connection>(http3_m, "Connection",
                              "HTTP/3 コネクション (Sans-IO)")
      .def_static(
          "create_client",
          [](const Http3Config& config) {
            auto conn = Http3Connection::create_client(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create HTTP/3 client connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Connection"),
          "クライアントとして接続を作成")
      .def_static(
          "create_server",
          [](const Http3Config& config) {
            auto conn = Http3Connection::create_server(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create HTTP/3 server connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Connection"),
          "サーバーとして接続を作成")
      .def(
          "receive_stream_data",
          [](Http3Connection& self, int64_t stream_id, nb::bytes data,
             bool fin) {
            return self.receive_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def receive_stream_data(self, stream_id: int, data: bytes, "
                  "fin: bool = False) -> int"),
          "QUIC ストリームからデータを受信")
      .def(
          "get_streams_to_send",
          [](Http3Connection& self) {
            auto streams = self.get_streams_to_send();
            std::vector<std::tuple<int64_t, nb::bytes, bool>> result;
            for (auto& [stream_id, data, fin] : streams) {
              result.emplace_back(
                  stream_id,
                  nb::bytes(reinterpret_cast<const char*>(data.data()),
                            data.size()),
                  fin);
            }
            return result;
          },
          nb::sig(
              "def get_streams_to_send(self) -> list[tuple[int, bytes, bool]]"),
          "送信すべきストリームデータを取得")
      .def("bind_control_stream", &Http3Connection::bind_control_stream,
           nb::arg("stream_id"),
           nb::sig("def bind_control_stream(self, stream_id: int) -> None"),
           "コントロールストリームを設定")
      .def("bind_qpack_encoder_stream",
           &Http3Connection::bind_qpack_encoder_stream, nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_encoder_stream(self, stream_id: int) -> None"),
           "QPACK エンコーダーストリームを設定")
      .def("bind_qpack_decoder_stream",
           &Http3Connection::bind_qpack_decoder_stream, nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_decoder_stream(self, stream_id: int) -> None"),
           "QPACK デコーダーストリームを設定")
      .def("submit_request", &Http3Connection::submit_request,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_request(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "リクエストを送信")
      .def("submit_response", &Http3Connection::submit_response,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_response(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "レスポンスを送信")
      .def(
          "send_data",
          [](Http3Connection& self, int64_t stream_id, nb::bytes data,
             bool fin) {
            self.send_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def send_data(self, stream_id: int, data: bytes, fin: bool "
                  "= False) -> None"),
          "ストリームにデータを送信")
      .def("reset_stream", &Http3Connection::reset_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "ストリームをリセット")
      .def("close_stream", &Http3Connection::close_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def close_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "QUIC ストリーム終了を nghttp3 に通知する")
      .def("goaway", &Http3Connection::goaway, nb::arg("id") = 0,
           nb::sig("def goaway(self, id: int = 0) -> None"), "GOAWAY を送信")
      .def("submit_trailers", &Http3Connection::submit_trailers,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_trailers(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "トレーラを送信")
      .def("submit_info", &Http3Connection::submit_info, nb::arg("stream_id"),
           nb::arg("headers"),
           nb::sig("def submit_info(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "1xx レスポンスを送信 (サーバーのみ)")
      .def("submit_shutdown_notice", &Http3Connection::submit_shutdown_notice,
           nb::sig("def submit_shutdown_notice(self) -> bool"),
           "graceful shutdown の開始通知を送信 (サーバーのみ)")
      .def("shutdown_stream_write", &Http3Connection::shutdown_stream_write,
           nb::arg("stream_id"),
           nb::sig("def shutdown_stream_write(self, stream_id: int) -> None"),
           "ストリームの書き込み側をシャットダウン")
      .def("next_event", &Http3Connection::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("get_required_streams", &Http3Connection::get_required_streams,
           nb::sig("def get_required_streams(self) -> list[tuple[str, bool]]"),
           "必要な QUIC ストリームのリストを取得")
      .def("is_closed", &Http3Connection::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def("stream_writable", &Http3Connection::stream_writable,
           nb::arg("stream_id"),
           nb::sig("def stream_writable(self, stream_id: int) -> int | None"),
           "ストリームが書き込み可能か確認")
      .def("stream_flushed", &Http3Connection::stream_flushed,
           nb::arg("stream_id"),
           nb::sig("def stream_flushed(self, stream_id: int) -> int | None"),
           "ストリームの全送信データが QUIC スタックに受け渡し済みか確認")
      .def(
          "frame_payload_left", &Http3Connection::frame_payload_left,
          nb::arg("stream_id"),
          nb::sig("def frame_payload_left(self, stream_id: int) -> int | None"),
          "受信中フレームのペイロード残量を取得")
      .def_prop_ro("drained", &Http3Connection::drained,
                   nb::sig("def drained(self) -> bool | None"),
                   "ドレイン状態か確認 (サーバーのみ)")
      .def("stream_priority", &Http3Connection::stream_priority,
           nb::arg("stream_id"),
           nb::sig("def stream_priority(self, stream_id: int) -> "
                   "tuple[int, bool] | None"),
           "ストリームの優先度を取得 (サーバーのみ)")
      .def("set_max_client_streams_bidi",
           &Http3Connection::set_max_client_streams_bidi,
           nb::arg("max_streams"),
           nb::sig("def set_max_client_streams_bidi(self, "
                   "max_streams: int) -> None"),
           "クライアントからの双方向ストリームの最大数を設定 (サーバーのみ)")
      .def("client_stream_priority", &Http3Connection::client_stream_priority,
           nb::arg("stream_id"), nb::arg("urgency"), nb::arg("incremental"),
           nb::sig("def client_stream_priority(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "クライアント起動双方向ストリームの優先度を設定 (クライアントのみ)")
      .def("server_stream_priority", &Http3Connection::server_stream_priority,
           nb::arg("stream_id"), nb::arg("urgency"), nb::arg("incremental"),
           nb::sig("def server_stream_priority(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "クライアント起動双方向ストリームの優先度を設定 (サーバーのみ)")
      .def("block_stream", &Http3Connection::block_stream, nb::arg("stream_id"),
           nb::sig("def block_stream(self, stream_id: int) -> None"),
           "ストリームの QUIC フロー制御ブロックを通知")
      .def("unblock_stream", &Http3Connection::unblock_stream,
           nb::arg("stream_id"),
           nb::sig("def unblock_stream(self, stream_id: int) -> bool"),
           "ストリームの QUIC フロー制御ブロック解除を通知")
      .def("max_concurrent_streams", &Http3Connection::max_concurrent_streams,
           nb::arg("n"),
           nb::sig("def max_concurrent_streams(self, n: int) -> None"),
           "同時ストリーム数のヒントを設定");

  // nghttp3 バージョン情報
  http3_m.def(
      "get_version",
      []() {
        auto* ver = nghttp3_version(0);
        return std::string(ver->version_str);
      },
      nb::sig("def get_version() -> str"), "nghttp3 のバージョンを取得");

  // RFC 9218 の Priority ヘッダー値のパース
  http3_m.def(
      "parse_priority",
      [](const std::string& value) -> std::optional<std::pair<uint32_t, bool>> {
        // nghttp3 のパーサは対象の構造体を初期化しないため、デフォルト
        // (urgency=3 / incremental=false) で初期化してからパースする
        // (RFC 9218 のデフォルト適用と同じ挙動)
        nghttp3_pri pri = {NGHTTP3_DEFAULT_URGENCY, 0};
        int rv = nghttp3_pri_parse_priority(
            &pri, reinterpret_cast<const uint8_t*>(value.data()), value.size());
        if (rv != 0) {
          return std::nullopt;
        }
        return std::make_pair(pri.urgency, pri.inc != 0);
      },
      nb::arg("value"),
      nb::sig("def parse_priority(value: str) -> tuple[int, bool] | None"),
      "RFC 9218 の Priority ヘッダー値をパース");
}

}  // namespace http3
}  // namespace webtransport
