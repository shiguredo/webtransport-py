/**
 * HTTP/2 バインディング (nghttp2 ラッパー)
 *
 * Sans-IO スタイルの HTTP/2 実装
 */

#include "http2.h"

#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace http2 {

// ========== Http2Connection 実装 ==========

Http2Connection::Http2Connection(bool is_server, const Http2Config& config)
    : is_server_(is_server), config_(config) {}

Http2Connection::~Http2Connection() {
  if (session_) {
    nghttp2_session_del(session_);
  }
}

Http2Connection::Http2Connection(Http2Connection&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      session_(other.session_),
      events_(std::move(other.events_)),
      send_buffer_(std::move(other.send_buffer_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      pending_headers_(std::move(other.pending_headers_)),
      closed_(other.closed_),
      goaway_sent_(other.goaway_sent_) {
  other.session_ = nullptr;
}

Http2Connection& Http2Connection::operator=(Http2Connection&& other) noexcept {
  if (this != &other) {
    if (session_)
      nghttp2_session_del(session_);

    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    session_ = other.session_;
    events_ = std::move(other.events_);
    send_buffer_ = std::move(other.send_buffer_);
    stream_buffers_ = std::move(other.stream_buffers_);
    pending_headers_ = std::move(other.pending_headers_);
    closed_ = other.closed_;
    goaway_sent_ = other.goaway_sent_;

    other.session_ = nullptr;
  }
  return *this;
}

std::unique_ptr<Http2Connection> Http2Connection::create_client(
    const Http2Config& config) {
  Http2Config client_config = config;
  client_config.is_server = false;
  auto conn = std::unique_ptr<Http2Connection>(
      new Http2Connection(false, client_config));
  if (!conn->initialize()) {
    return nullptr;
  }
  return conn;
}

std::unique_ptr<Http2Connection> Http2Connection::create_server(
    const Http2Config& config) {
  Http2Config server_config = config;
  server_config.is_server = true;
  auto conn = std::unique_ptr<Http2Connection>(
      new Http2Connection(true, server_config));
  if (!conn->initialize()) {
    return nullptr;
  }
  return conn;
}

bool Http2Connection::initialize() {
  nghttp2_session_callbacks* callbacks = nullptr;

  int rv = nghttp2_session_callbacks_new(&callbacks);
  if (rv != 0) {
    return false;
  }

  // コールバックを設定
  nghttp2_session_callbacks_set_send_callback(callbacks, send_callback);
  nghttp2_session_callbacks_set_on_frame_recv_callback(callbacks,
                                                       on_frame_recv_callback);
  nghttp2_session_callbacks_set_on_data_chunk_recv_callback(
      callbacks, on_data_chunk_recv_callback);
  nghttp2_session_callbacks_set_on_stream_close_callback(
      callbacks, on_stream_close_callback);
  nghttp2_session_callbacks_set_on_header_callback(callbacks,
                                                   on_header_callback);
  nghttp2_session_callbacks_set_on_begin_headers_callback(
      callbacks, on_begin_headers_callback);

  // セッションを作成
  if (is_server_) {
    rv = nghttp2_session_server_new(&session_, callbacks, this);
  } else {
    rv = nghttp2_session_client_new(&session_, callbacks, this);
  }

  nghttp2_session_callbacks_del(callbacks);

  if (rv != 0) {
    return false;
  }

  // 設定を送信
  nghttp2_settings_entry iv[] = {
      {NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS, config_.max_concurrent_streams},
      {NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE, config_.initial_window_size},
      {NGHTTP2_SETTINGS_MAX_FRAME_SIZE, config_.max_frame_size},
      {NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE, config_.max_header_list_size},
  };

  rv = nghttp2_submit_settings(session_, NGHTTP2_FLAG_NONE, iv,
                               sizeof(iv) / sizeof(iv[0]));
  if (rv != 0) {
    return false;
  }

  return true;
}

size_t Http2Connection::receive(const std::vector<uint8_t>& data) {
  if (!session_ || closed_) {
    return 0;
  }

  ssize_t rv = nghttp2_session_mem_recv(session_, data.data(), data.size());
  if (rv < 0) {
    closed_ = true;
    return 0;
  }

  return static_cast<size_t>(rv);
}

std::optional<std::vector<uint8_t>> Http2Connection::send() {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  // セッションから送信データを取得
  const uint8_t* data = nullptr;
  ssize_t len = nghttp2_session_mem_send(session_, &data);

  if (len < 0) {
    closed_ = true;
    return std::nullopt;
  }

  if (len == 0) {
    return std::nullopt;
  }

  return std::vector<uint8_t>(data, data + len);
}

int32_t Http2Connection::submit_request(
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!session_ || closed_ || is_server_) {
    return -1;
  }

  // ヘッダーを nghttp2_nv に変換
  std::vector<nghttp2_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp2_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP2_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  int32_t stream_id = nghttp2_submit_request(session_, nullptr, nva.data(),
                                             nva.size(), nullptr, nullptr);

  if (stream_id < 0) {
    return -1;
  }

  stream_buffers_[stream_id] = {};
  return stream_id;
}

void Http2Connection::submit_response(
    int32_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!session_ || closed_ || !is_server_) {
    return;
  }

  // ヘッダーを nghttp2_nv に変換
  std::vector<nghttp2_nv> nva;
  nva.reserve(headers.size());

  for (const auto& [name, value] : headers) {
    nghttp2_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP2_NV_FLAG_NONE;
    nva.push_back(nv);
  }

  // データプロバイダの設定
  nghttp2_data_provider data_prd;
  data_prd.source.ptr = this;
  data_prd.read_callback = data_source_read_callback;

  stream_buffers_[stream_id] = {};

  nghttp2_submit_response(session_, stream_id, nva.data(), nva.size(),
                          &data_prd);
}

void Http2Connection::send_data(int32_t stream_id,
                                const std::vector<uint8_t>& data,
                                bool eof) {
  if (!session_ || closed_) {
    return;
  }

  stream_buffers_[stream_id].push_back({data, eof});
  nghttp2_session_resume_data(session_, stream_id);
}

void Http2Connection::reset_stream(int32_t stream_id, uint32_t error_code) {
  if (!session_ || closed_) {
    return;
  }

  nghttp2_submit_rst_stream(session_, NGHTTP2_FLAG_NONE, stream_id, error_code);
  stream_buffers_.erase(stream_id);
}

void Http2Connection::goaway(uint32_t error_code) {
  if (!session_ || closed_ || goaway_sent_) {
    return;
  }

  int32_t last_stream_id = nghttp2_session_get_last_proc_stream_id(session_);
  nghttp2_submit_goaway(session_, NGHTTP2_FLAG_NONE, last_stream_id, error_code,
                        nullptr, 0);
  goaway_sent_ = true;
}

void Http2Connection::ping() {
  if (!session_ || closed_) {
    return;
  }

  nghttp2_submit_ping(session_, NGHTTP2_FLAG_NONE, nullptr);
}

std::optional<Http2Event> Http2Connection::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  auto event = std::move(events_.front());
  events_.pop_front();
  return event;
}

bool Http2Connection::want_write() const {
  if (!session_ || closed_) {
    return false;
  }
  return nghttp2_session_want_write(session_) != 0;
}

bool Http2Connection::is_closed() const {
  return closed_;
}

// ========== セッション状態確認 API ==========

std::optional<std::map<std::string, uint32_t>>
Http2Connection::remote_settings() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  // ピアが送信した SETTINGS の値 (受信前は nghttp2 のデフォルト値。
  // max_concurrent_streams のみセッション生成時に 100 が設定され、最初の
  // SETTINGS 受信時にリセットされた後、フレームのエントリ値が適用される)
  return std::map<std::string, uint32_t>{
      {"initial_window_size",
       nghttp2_session_get_remote_settings(
           session_, NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE)},
      {"max_concurrent_streams",
       nghttp2_session_get_remote_settings(
           session_, NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS)},
      {"max_frame_size", nghttp2_session_get_remote_settings(
                             session_, NGHTTP2_SETTINGS_MAX_FRAME_SIZE)},
      {"max_header_list_size",
       nghttp2_session_get_remote_settings(
           session_, NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE)},
  };
}

std::optional<std::map<std::string, uint32_t>> Http2Connection::local_settings()
    const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  // ピアが ACK したローカルの SETTINGS の値 (ACK 前は nghttp2 の
  // デフォルト値)
  return std::map<std::string, uint32_t>{
      {"initial_window_size",
       nghttp2_session_get_local_settings(
           session_, NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE)},
      {"max_concurrent_streams",
       nghttp2_session_get_local_settings(
           session_, NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS)},
      {"max_frame_size", nghttp2_session_get_local_settings(
                             session_, NGHTTP2_SETTINGS_MAX_FRAME_SIZE)},
      {"max_header_list_size",
       nghttp2_session_get_local_settings(
           session_, NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE)},
  };
}

std::optional<size_t> Http2Connection::outbound_queue_size() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }
  return nghttp2_session_get_outbound_queue_size(session_);
}

std::optional<int32_t> Http2Connection::remote_window_size() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }
  return nghttp2_session_get_remote_window_size(session_);
}

std::optional<int32_t> Http2Connection::local_window_size() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }
  return nghttp2_session_get_local_window_size(session_);
}

std::optional<int32_t> Http2Connection::effective_recv_data_length() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }
  return nghttp2_session_get_effective_recv_data_length(session_);
}

std::optional<bool> Http2Connection::request_allowed() const {
  if (!session_ || closed_) {
    return std::nullopt;
  }
  return nghttp2_session_check_request_allowed(session_) != 0;
}

std::optional<int32_t> Http2Connection::stream_remote_window_size(
    int32_t stream_id) const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  int32_t window_size =
      nghttp2_session_get_stream_remote_window_size(session_, stream_id);
  if (window_size < 0) {
    // ストリームが存在しない場合は None を返す (完全に閉じたストリームも
    // nghttp2 の管理から外れて存在しなくなる)
    return std::nullopt;
  }
  return window_size;
}

std::optional<int32_t> Http2Connection::stream_local_window_size(
    int32_t stream_id) const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  int32_t window_size =
      nghttp2_session_get_stream_local_window_size(session_, stream_id);
  if (window_size < 0) {
    return std::nullopt;
  }
  return window_size;
}

std::optional<int32_t> Http2Connection::stream_effective_recv_data_length(
    int32_t stream_id) const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  int32_t recv_data_length =
      nghttp2_session_get_stream_effective_recv_data_length(session_,
                                                            stream_id);
  if (recv_data_length < 0) {
    return std::nullopt;
  }
  return recv_data_length;
}

std::optional<bool> Http2Connection::stream_local_close(
    int32_t stream_id) const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  int rv = nghttp2_session_get_stream_local_close(session_, stream_id);
  if (rv < 0) {
    return std::nullopt;
  }
  return rv != 0;
}

std::optional<bool> Http2Connection::stream_remote_close(
    int32_t stream_id) const {
  if (!session_ || closed_) {
    return std::nullopt;
  }

  int rv = nghttp2_session_get_stream_remote_close(session_, stream_id);
  if (rv < 0) {
    return std::nullopt;
  }
  return rv != 0;
}

void Http2Connection::push_event(Http2Event event) {
  events_.push_back(std::move(event));
}

// ========== nghttp2 コールバック ==========

ssize_t Http2Connection::send_callback(nghttp2_session* session,
                                       const uint8_t* data,
                                       size_t length,
                                       int flags,
                                       void* user_data) {
  // nghttp2_session_mem_send を使用するため、このコールバックは使われない
  return NGHTTP2_ERR_WOULDBLOCK;
}

int Http2Connection::on_frame_recv_callback(nghttp2_session* session,
                                            const nghttp2_frame* frame,
                                            void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);

  switch (frame->hd.type) {
    case NGHTTP2_HEADERS:
      if (frame->hd.flags & NGHTTP2_FLAG_END_HEADERS) {
        // ヘッダー受信完了
        auto it = self->pending_headers_.find(frame->hd.stream_id);
        if (it != self->pending_headers_.end()) {
          Http2Event event;
          event.type = Http2EventType::Headers;
          event.stream_id = frame->hd.stream_id;
          event.headers = std::move(it->second);
          self->push_event(std::move(event));
          self->pending_headers_.erase(it);
        }
      }
      if (frame->hd.flags & NGHTTP2_FLAG_END_STREAM) {
        Http2Event event;
        event.type = Http2EventType::StreamEnd;
        event.stream_id = frame->hd.stream_id;
        self->push_event(std::move(event));
      }
      break;

    case NGHTTP2_DATA:
      if (frame->hd.flags & NGHTTP2_FLAG_END_STREAM) {
        Http2Event event;
        event.type = Http2EventType::StreamEnd;
        event.stream_id = frame->hd.stream_id;
        self->push_event(std::move(event));
      }
      break;

    case NGHTTP2_SETTINGS:
      if (!(frame->hd.flags & NGHTTP2_FLAG_ACK)) {
        Http2Event event;
        event.type = Http2EventType::Settings;
        self->push_event(std::move(event));
      }
      break;

    case NGHTTP2_PING:
      if (!(frame->hd.flags & NGHTTP2_FLAG_ACK)) {
        Http2Event event;
        event.type = Http2EventType::Ping;
        self->push_event(std::move(event));
      }
      break;

    case NGHTTP2_GOAWAY: {
      Http2Event event;
      event.type = Http2EventType::GoAway;
      event.error_code = frame->goaway.error_code;
      event.last_stream_id = frame->goaway.last_stream_id;
      self->push_event(std::move(event));
      self->closed_ = true;
    } break;

    case NGHTTP2_RST_STREAM: {
      Http2Event event;
      event.type = Http2EventType::StreamReset;
      event.stream_id = frame->hd.stream_id;
      event.error_code = frame->rst_stream.error_code;
      self->push_event(std::move(event));
      self->stream_buffers_.erase(frame->hd.stream_id);
    } break;

    case NGHTTP2_WINDOW_UPDATE: {
      Http2Event event;
      event.type = Http2EventType::WindowUpdate;
      event.stream_id = frame->hd.stream_id;
      self->push_event(std::move(event));
    } break;

    default:
      break;
  }

  return 0;
}

int Http2Connection::on_data_chunk_recv_callback(nghttp2_session* session,
                                                 uint8_t flags,
                                                 int32_t stream_id,
                                                 const uint8_t* data,
                                                 size_t len,
                                                 void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);

  Http2Event event;
  event.type = Http2EventType::Data;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + len);
  self->push_event(std::move(event));

  return 0;
}

int Http2Connection::on_stream_close_callback(nghttp2_session* session,
                                              int32_t stream_id,
                                              uint32_t error_code,
                                              void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);
  self->stream_buffers_.erase(stream_id);
  self->pending_headers_.erase(stream_id);
  return 0;
}

int Http2Connection::on_header_callback(nghttp2_session* session,
                                        const nghttp2_frame* frame,
                                        const uint8_t* name,
                                        size_t namelen,
                                        const uint8_t* value,
                                        size_t valuelen,
                                        uint8_t flags,
                                        void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);

  if (frame->hd.type == NGHTTP2_HEADERS) {
    std::string name_str(reinterpret_cast<const char*>(name), namelen);
    std::string value_str(reinterpret_cast<const char*>(value), valuelen);
    self->pending_headers_[frame->hd.stream_id].emplace_back(
        std::move(name_str), std::move(value_str));
  }

  return 0;
}

int Http2Connection::on_begin_headers_callback(nghttp2_session* session,
                                               const nghttp2_frame* frame,
                                               void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);

  if (frame->hd.type == NGHTTP2_HEADERS) {
    self->pending_headers_[frame->hd.stream_id] = {};
  }

  return 0;
}

ssize_t Http2Connection::data_source_read_callback(nghttp2_session* session,
                                                   int32_t stream_id,
                                                   uint8_t* buf,
                                                   size_t length,
                                                   uint32_t* data_flags,
                                                   nghttp2_data_source* source,
                                                   void* user_data) {
  auto* self = static_cast<Http2Connection*>(user_data);

  auto it = self->stream_buffers_.find(stream_id);
  if (it == self->stream_buffers_.end() || it->second.empty()) {
    return NGHTTP2_ERR_DEFERRED;
  }

  auto& buffers = it->second;
  auto& front = buffers.front();

  size_t copy_len = std::min(length, front.data.size());
  std::memcpy(buf, front.data.data(), copy_len);

  if (copy_len < front.data.size()) {
    front.data.erase(front.data.begin(), front.data.begin() + copy_len);
  } else {
    bool is_eof = front.eof;
    buffers.pop_front();

    if (is_eof) {
      *data_flags |= NGHTTP2_DATA_FLAG_EOF;
    }
  }

  return static_cast<ssize_t>(copy_len);
}

// ========== Python バインディング ==========

void bind_http2(nb::module_& m) {
  auto http2_m = m.def_submodule("http2", "HTTP/2 protocol (nghttp2)");

  // Http2Config
  nb::class_<Http2Config>(http2_m, "Config", "HTTP/2 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("initial_window_size", &Http2Config::initial_window_size,
              "初期ウィンドウサイズ")
      .def_rw("max_concurrent_streams", &Http2Config::max_concurrent_streams,
              "最大同時ストリーム数")
      .def_rw("max_frame_size", &Http2Config::max_frame_size,
              "最大フレームサイズ")
      .def_rw("max_header_list_size", &Http2Config::max_header_list_size,
              "最大ヘッダーリストサイズ")
      .def_rw("is_server", &Http2Config::is_server, "サーバーモード")
      .def_rw("send_preface", &Http2Config::send_preface,
              "HTTP/2 プリフェイスを送信するか");

  // Http2EventType
  nb::enum_<Http2EventType>(http2_m, "EventType", "HTTP/2 イベント種別")
      .value("HEADERS", Http2EventType::Headers)
      .value("DATA", Http2EventType::Data)
      .value("STREAM_END", Http2EventType::StreamEnd)
      .value("STREAM_RESET", Http2EventType::StreamReset)
      .value("GO_AWAY", Http2EventType::GoAway)
      .value("WINDOW_UPDATE", Http2EventType::WindowUpdate)
      .value("SETTINGS", Http2EventType::Settings)
      .value("PING", Http2EventType::Ping);

  // Http2Event
  nb::class_<Http2Event>(http2_m, "Event", "HTTP/2 イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &Http2Event::type, "イベント種別")
      .def_ro("stream_id", &Http2Event::stream_id, "ストリーム ID")
      .def_ro("headers", &Http2Event::headers, "ヘッダー")
      .def_prop_ro(
          "data",
          [](const Http2Event& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "データ")
      .def_ro("error_code", &Http2Event::error_code, "エラーコード")
      .def_ro("last_stream_id", &Http2Event::last_stream_id,
              "GOAWAY の last_stream_id");

  // Http2Connection
  nb::class_<Http2Connection>(http2_m, "Connection",
                              "HTTP/2 コネクション (Sans-IO)")
      .def_static(
          "create_client",
          [](const Http2Config& config) {
            auto conn = Http2Connection::create_client(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create HTTP/2 client connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Connection"),
          "クライアントとして接続を作成")
      .def_static(
          "create_server",
          [](const Http2Config& config) {
            auto conn = Http2Connection::create_server(config);
            if (!conn) {
              throw std::runtime_error(
                  "Failed to create HTTP/2 server connection");
            }
            return conn.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Connection"),
          "サーバーとして接続を作成")
      .def(
          "receive",
          [](Http2Connection& self, nb::bytes data) {
            return self.receive(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"), nb::sig("def receive(self, data: bytes) -> int"),
          "受信したデータを処理")
      .def(
          "send",
          [](Http2Connection& self) -> nb::object {
            auto result = self.send();
            if (result) {
              return nb::bytes(reinterpret_cast<const char*>(result->data()),
                               result->size());
            }
            return nb::none();
          },
          nb::sig("def send(self) -> bytes | None"), "送信すべきデータを取得")
      .def("submit_request", &Http2Connection::submit_request,
           nb::arg("headers"),
           nb::sig("def submit_request(self, headers: list[tuple[str, str]]) "
                   "-> int"),
           "リクエストを送信")
      .def("submit_response", &Http2Connection::submit_response,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_response(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> None"),
           "レスポンスを送信")
      .def(
          "send_data",
          [](Http2Connection& self, int32_t stream_id, nb::bytes data,
             bool eof) {
            self.send_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                eof);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("eof") = false,
          nb::sig("def send_data(self, stream_id: int, data: bytes, eof: bool "
                  "= False) -> None"),
          "ストリームにデータを送信")
      .def("reset_stream", &Http2Connection::reset_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "ストリームをリセット")
      .def("goaway", &Http2Connection::goaway, nb::arg("error_code") = 0,
           nb::sig("def goaway(self, error_code: int = 0) -> None"),
           "GOAWAY を送信")
      .def("ping", &Http2Connection::ping, nb::sig("def ping(self) -> None"),
           "PING を送信")
      .def("next_event", &Http2Connection::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("want_write", &Http2Connection::want_write,
           nb::sig("def want_write(self) -> bool"), "送信待ちデータがあるか")
      .def("is_closed", &Http2Connection::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def_prop_ro(
          "remote_settings", &Http2Connection::remote_settings,
          nb::sig("def remote_settings(self) -> dict[str, int] | None"),
          "ピアの SETTINGS の値を取得")
      .def_prop_ro("local_settings", &Http2Connection::local_settings,
                   nb::sig("def local_settings(self) -> dict[str, int] | None"),
                   "ローカルの SETTINGS の値を取得")
      .def_prop_ro("outbound_queue_size", &Http2Connection::outbound_queue_size,
                   nb::sig("def outbound_queue_size(self) -> int | None"),
                   "送信キューのフレーム数を取得")
      .def_prop_ro("remote_window_size", &Http2Connection::remote_window_size,
                   nb::sig("def remote_window_size(self) -> int | None"),
                   "コネクションのリモートウィンドウ残量を取得")
      .def_prop_ro("local_window_size", &Http2Connection::local_window_size,
                   nb::sig("def local_window_size(self) -> int | None"),
                   "コネクションのローカルウィンドウ残量を取得")
      .def_prop_ro(
          "effective_recv_data_length",
          &Http2Connection::effective_recv_data_length,
          nb::sig("def effective_recv_data_length(self) -> int | None"),
          "WINDOW_UPDATE 未送信の受信 DATA バイト数を取得")
      .def_prop_ro("request_allowed", &Http2Connection::request_allowed,
                   nb::sig("def request_allowed(self) -> bool | None"),
                   "新しいリクエストを送信できるかを取得")
      .def("stream_remote_window_size",
           &Http2Connection::stream_remote_window_size, nb::arg("stream_id"),
           nb::sig("def stream_remote_window_size(self, stream_id: int) -> "
                   "int | None"),
           "ストリームのリモートウィンドウ残量を取得")
      .def("stream_local_window_size",
           &Http2Connection::stream_local_window_size, nb::arg("stream_id"),
           nb::sig("def stream_local_window_size(self, stream_id: int) -> "
                   "int | None"),
           "ストリームのローカルウィンドウ残量を取得")
      .def("stream_effective_recv_data_length",
           &Http2Connection::stream_effective_recv_data_length,
           nb::arg("stream_id"),
           nb::sig("def stream_effective_recv_data_length(self, stream_id: "
                   "int) -> int | None"),
           "ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数を取得")
      .def("stream_local_close", &Http2Connection::stream_local_close,
           nb::arg("stream_id"),
           nb::sig(
               "def stream_local_close(self, stream_id: int) -> bool | None"),
           "ストリームのローカル側が half-closed かを取得")
      .def("stream_remote_close", &Http2Connection::stream_remote_close,
           nb::arg("stream_id"),
           nb::sig(
               "def stream_remote_close(self, stream_id: int) -> bool | None"),
           "ストリームのリモート側が half-closed かを取得");

  // nghttp2 バージョン情報
  http2_m.def(
      "get_version",
      []() {
        auto* ver = nghttp2_version(0);
        return std::string(ver->version_str);
      },
      nb::sig("def get_version() -> str"), "nghttp2 のバージョンを取得");
}

}  // namespace http2
}  // namespace webtransport
