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
      closed_(other.closed_) {
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

  nghttp3_ssize rv = nghttp3_conn_read_stream2(
      conn_, stream_id, data.data(), data.size(), fin ? 1 : 0, 0);
  if (rv < 0) {
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
  // HTTP/3 requires 3 unidirectional streams
  // control (server -> client or client -> server)
  // qpack encoder
  // qpack decoder
  return {
      {"control", false},        // false = unidirectional
      {"qpack_encoder", false},  // false = unidirectional
      {"qpack_decoder", false},  // false = unidirectional
  };
}

bool Http3Connection::is_closed() const {
  return closed_;
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

  // 送信済みで FIN なしのバッファは捨てて次へ進む
  // (連続 send_data でキューが積まれたときに 0 返却で nghttp3 が abort するのを防ぐ)
  while (!buffers.empty()) {
    auto& buffer = buffers.front();
    size_t remaining = buffer.data.size() - buffer.offset;
    if (remaining == 0) {
      if (buffer.fin) {
        *pflags |= NGHTTP3_DATA_FLAG_EOF;
        return 0;
      }
      buffers.pop_front();
      continue;
    }

    // データを返す（オフセットから開始）
    vec[0].base = const_cast<uint8_t*>(buffer.data.data() + buffer.offset);
    vec[0].len = remaining;

    // オフセットを更新（データはまだ削除しない - acked_stream_data_cb で削除）
    buffer.offset = buffer.data.size();

    if (buffer.fin && buffers.size() == 1) {
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
      .def("next_event", &Http3Connection::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("get_required_streams", &Http3Connection::get_required_streams,
           nb::sig("def get_required_streams(self) -> list[tuple[str, bool]]"),
           "必要な QUIC ストリームのリストを取得")
      .def("is_closed", &Http3Connection::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか");

  // nghttp3 バージョン情報
  http3_m.def(
      "get_version",
      []() {
        auto* ver = nghttp3_version(0);
        return std::string(ver->version_str);
      },
      nb::sig("def get_version() -> str"), "nghttp3 のバージョンを取得");
}

}  // namespace http3
}  // namespace webtransport
