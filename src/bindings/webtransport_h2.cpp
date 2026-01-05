/**
 * WebTransport over HTTP/2 バインディング実装 (draft-ietf-webtrans-http2-13)
 *
 * Capsule Protocol (RFC 9297) を使用した実装
 */

#include "webtransport_h2.h"

#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace h2 {

// ========== Varint エンコード/デコード (QUIC 形式) ==========

std::vector<uint8_t> H2Session::encode_varint(uint64_t value) {
  std::vector<uint8_t> result;

  if (value < 64) {
    result.push_back(static_cast<uint8_t>(value));
  } else if (value < 16384) {
    result.push_back(static_cast<uint8_t>((value >> 8) | 0x40));
    result.push_back(static_cast<uint8_t>(value & 0xFF));
  } else if (value < 1073741824) {
    result.push_back(static_cast<uint8_t>((value >> 24) | 0x80));
    result.push_back(static_cast<uint8_t>((value >> 16) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
    result.push_back(static_cast<uint8_t>(value & 0xFF));
  } else {
    result.push_back(static_cast<uint8_t>((value >> 56) | 0xC0));
    result.push_back(static_cast<uint8_t>((value >> 48) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 40) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 32) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 24) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 16) & 0xFF));
    result.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
    result.push_back(static_cast<uint8_t>(value & 0xFF));
  }

  return result;
}

std::optional<std::pair<uint64_t, size_t>> H2Session::decode_varint(
    const uint8_t* data,
    size_t length) {
  if (length == 0) {
    return std::nullopt;
  }

  uint8_t first = data[0];
  uint8_t prefix = first >> 6;

  size_t var_length = 1 << prefix;
  if (length < var_length) {
    return std::nullopt;
  }

  uint64_t value = first & 0x3F;
  for (size_t i = 1; i < var_length; ++i) {
    value = (value << 8) | data[i];
  }

  return std::make_pair(value, var_length);
}

// ========== Capsule エンコード/デコード ==========

std::vector<uint8_t> H2Session::encode_capsule(
    CapsuleType type,
    const std::vector<uint8_t>& payload) {
  std::vector<uint8_t> result;

  auto type_bytes = encode_varint(static_cast<uint64_t>(type));
  auto length_bytes = encode_varint(payload.size());

  result.insert(result.end(), type_bytes.begin(), type_bytes.end());
  result.insert(result.end(), length_bytes.begin(), length_bytes.end());
  result.insert(result.end(), payload.begin(), payload.end());

  return result;
}

void H2Session::process_capsules(int32_t session_id,
                                 const uint8_t* data,
                                 size_t length) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // バッファに追加
  wt_session->capsule_buffer.insert(wt_session->capsule_buffer.end(), data,
                                    data + length);

  // Capsule をパース
  while (!wt_session->capsule_buffer.empty()) {
    const uint8_t* buf = wt_session->capsule_buffer.data();
    size_t buf_len = wt_session->capsule_buffer.size();

    // Type をデコード
    auto type_result = decode_varint(buf, buf_len);
    if (!type_result) {
      break;
    }
    auto [type_value, type_len] = *type_result;

    // Length をデコード
    if (buf_len < type_len) {
      break;
    }
    auto length_result = decode_varint(buf + type_len, buf_len - type_len);
    if (!length_result) {
      break;
    }
    auto [payload_len, length_len] = *length_result;

    // ペイロードが揃っているかチェック
    size_t header_len = type_len + length_len;
    if (buf_len < header_len + payload_len) {
      break;
    }

    // Capsule を処理
    CapsuleType capsule_type = static_cast<CapsuleType>(type_value);
    const uint8_t* payload = buf + header_len;
    process_capsule(session_id, capsule_type, payload,
                    static_cast<size_t>(payload_len));

    // 処理済みデータを削除
    wt_session->capsule_buffer.erase(
        wt_session->capsule_buffer.begin(),
        wt_session->capsule_buffer.begin() +
            static_cast<std::ptrdiff_t>(header_len + payload_len));
  }
}

void H2Session::process_capsule(int32_t session_id,
                                CapsuleType type,
                                const uint8_t* payload,
                                size_t length) {
  switch (type) {
    case CapsuleType::WtStream:
      handle_wt_stream(session_id, false, payload, length);
      break;
    case CapsuleType::WtStreamFin:
      handle_wt_stream(session_id, true, payload, length);
      break;
    case CapsuleType::WtResetStream:
      handle_wt_reset_stream(session_id, payload, length);
      break;
    case CapsuleType::WtStopSending:
      handle_wt_stop_sending(session_id, payload, length);
      break;
    case CapsuleType::WtMaxData:
      handle_wt_max_data(session_id, payload, length);
      break;
    case CapsuleType::WtMaxStreamData:
      handle_wt_max_stream_data(session_id, payload, length);
      break;
    case CapsuleType::WtMaxStreamsBidi:
      handle_wt_max_streams(session_id, true, payload, length);
      break;
    case CapsuleType::WtMaxStreamsUni:
      handle_wt_max_streams(session_id, false, payload, length);
      break;
    case CapsuleType::Datagram:
      handle_datagram(session_id, payload, length);
      break;
    case CapsuleType::WtCloseSession:
      handle_wt_close_session(session_id, payload, length);
      break;
    case CapsuleType::WtDrainSession:
      handle_wt_drain_session(session_id);
      break;
    case CapsuleType::Padding:
    case CapsuleType::WtDataBlocked:
    case CapsuleType::WtStreamDataBlocked:
    case CapsuleType::WtStreamsBlockedBidi:
    case CapsuleType::WtStreamsBlockedUni:
      // これらは無視
      break;
  }
}

// ========== Capsule ハンドラー ==========

void H2Session::handle_wt_stream(int32_t session_id,
                                 bool fin,
                                 const uint8_t* payload,
                                 size_t length) {
  if (length == 0) {
    return;
  }

  // Stream ID をデコード
  auto stream_id_result = decode_varint(payload, length);
  if (!stream_id_result) {
    return;
  }
  auto [stream_id, stream_id_len] = *stream_id_result;

  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // ストリームが存在しない場合は作成
  if (wt_session->streams.find(stream_id) == wt_session->streams.end()) {
    WtStreamInfo info;
    info.stream_id = stream_id;
    info.is_local = false;
    info.is_unidirectional = (stream_id & 0x02) != 0;
    info.max_stream_data_local = config_.wt_initial_max_stream_data;
    info.max_stream_data_remote = config_.wt_initial_max_stream_data;
    wt_session->streams[stream_id] = info;
  }

  // データ部分
  const uint8_t* stream_data = payload + stream_id_len;
  size_t data_len = length - stream_id_len;

  // データがある場合、または FIN フラグがある場合のみイベントを発行
  // ストリーム開始のみの capsule (データなし、FIN なし) はイベントを発行しない
  if (data_len > 0 || fin) {
    H2Event event;
    event.type = H2EventType::StreamData;
    event.session_id = session_id;
    event.stream_id = stream_id;
    event.data.assign(stream_data, stream_data + data_len);
    event.fin = fin;
    push_event(std::move(event));
  }

  // フロー制御更新
  auto& stream_info = wt_session->streams[stream_id];
  stream_info.bytes_received += data_len;
  wt_session->bytes_received += data_len;
}

void H2Session::handle_wt_reset_stream(int32_t session_id,
                                       const uint8_t* payload,
                                       size_t length) {
  size_t offset = 0;

  // Stream ID
  auto stream_id_result = decode_varint(payload + offset, length - offset);
  if (!stream_id_result) {
    return;
  }
  auto [stream_id, stream_id_len] = *stream_id_result;
  offset += stream_id_len;

  // Error Code
  auto error_code_result = decode_varint(payload + offset, length - offset);
  if (!error_code_result) {
    return;
  }
  auto [error_code, error_code_len] = *error_code_result;
  offset += error_code_len;

  // Reliable Size (無視)

  H2Event event;
  event.type = H2EventType::StreamReset;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.error_code = static_cast<uint32_t>(error_code);
  push_event(std::move(event));
}

void H2Session::handle_wt_stop_sending(int32_t session_id,
                                       const uint8_t* payload,
                                       size_t length) {
  size_t offset = 0;

  // Stream ID
  auto stream_id_result = decode_varint(payload + offset, length - offset);
  if (!stream_id_result) {
    return;
  }
  auto [stream_id, stream_id_len] = *stream_id_result;
  offset += stream_id_len;

  // Error Code
  auto error_code_result = decode_varint(payload + offset, length - offset);
  if (!error_code_result) {
    return;
  }
  auto [error_code, error_code_len] = *error_code_result;

  H2Event event;
  event.type = H2EventType::StopSending;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.error_code = static_cast<uint32_t>(error_code);
  push_event(std::move(event));
}

void H2Session::handle_wt_max_data(int32_t session_id,
                                   const uint8_t* payload,
                                   size_t length) {
  auto max_data_result = decode_varint(payload, length);
  if (!max_data_result) {
    return;
  }
  auto [max_data, max_data_len] = *max_data_result;

  auto* wt_session = get_wt_session(session_id);
  if (wt_session && max_data > wt_session->max_data_local) {
    wt_session->max_data_local = max_data;
  }
}

void H2Session::handle_wt_max_stream_data(int32_t session_id,
                                          const uint8_t* payload,
                                          size_t length) {
  size_t offset = 0;

  // Stream ID
  auto stream_id_result = decode_varint(payload + offset, length - offset);
  if (!stream_id_result) {
    return;
  }
  auto [stream_id, stream_id_len] = *stream_id_result;
  offset += stream_id_len;

  // Max Stream Data
  auto max_data_result = decode_varint(payload + offset, length - offset);
  if (!max_data_result) {
    return;
  }
  auto [max_data, max_data_len] = *max_data_result;

  auto* wt_session = get_wt_session(session_id);
  if (wt_session) {
    auto it = wt_session->streams.find(stream_id);
    if (it != wt_session->streams.end() &&
        max_data > it->second.max_stream_data_local) {
      it->second.max_stream_data_local = max_data;
    }
  }
}

void H2Session::handle_wt_max_streams(int32_t session_id,
                                      bool is_bidi,
                                      const uint8_t* payload,
                                      size_t length) {
  auto max_streams_result = decode_varint(payload, length);
  if (!max_streams_result) {
    return;
  }
  auto [max_streams, max_streams_len] = *max_streams_result;

  auto* wt_session = get_wt_session(session_id);
  if (wt_session) {
    if (is_bidi) {
      if (max_streams > wt_session->max_streams_bidi_local) {
        wt_session->max_streams_bidi_local = max_streams;
      }
    } else {
      if (max_streams > wt_session->max_streams_uni_local) {
        wt_session->max_streams_uni_local = max_streams;
      }
    }
  }
}

void H2Session::handle_datagram(int32_t session_id,
                                const uint8_t* payload,
                                size_t length) {
  H2Event event;
  event.type = H2EventType::Datagram;
  event.session_id = session_id;
  event.data.assign(payload, payload + length);
  push_event(std::move(event));
}

void H2Session::handle_wt_close_session(int32_t session_id,
                                        const uint8_t* payload,
                                        size_t length) {
  uint32_t error_code = 0;
  std::string error_message;

  if (length >= 4) {
    error_code = (static_cast<uint32_t>(payload[0]) << 24) |
                 (static_cast<uint32_t>(payload[1]) << 16) |
                 (static_cast<uint32_t>(payload[2]) << 8) |
                 static_cast<uint32_t>(payload[3]);
    if (length > 4) {
      error_message.assign(reinterpret_cast<const char*>(payload + 4),
                           length - 4);
    }
  }

  H2Event event;
  event.type = H2EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = error_code;
  event.error_message = error_message;
  push_event(std::move(event));

  wt_sessions_.erase(session_id);
}

void H2Session::handle_wt_drain_session(int32_t session_id) {
  H2Event event;
  event.type = H2EventType::SessionDraining;
  event.session_id = session_id;
  push_event(std::move(event));
}

// ========== Capsule 送信 ==========

void H2Session::send_capsule(int32_t session_id,
                             CapsuleType type,
                             const std::vector<uint8_t>& payload) {
  auto capsule = encode_capsule(type, payload);
  http2_stream_buffers_[session_id].push_back(std::move(capsule));
  nghttp2_session_resume_data(session_, session_id);
}

// ========== ストリーム ID 割り当て ==========

uint64_t H2Session::allocate_stream_id(int32_t session_id,
                                       bool is_unidirectional) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return UINT64_MAX;
  }

  // QUIC 互換ストリーム ID
  // Bit 0: initiator (0 = client, 1 = server)
  // Bit 1: directionality (0 = bidi, 1 = uni)
  uint64_t initiator_bit = is_server_ ? 1 : 0;
  uint64_t dir_bit = is_unidirectional ? 2 : 0;

  uint64_t stream_id;
  if (is_unidirectional) {
    stream_id = (wt_session->next_uni_stream_id << 2) | dir_bit | initiator_bit;
    wt_session->next_uni_stream_id++;
    wt_session->streams_uni_opened++;
  } else {
    stream_id =
        (wt_session->next_bidi_stream_id << 2) | dir_bit | initiator_bit;
    wt_session->next_bidi_stream_id++;
    wt_session->streams_bidi_opened++;
  }

  return stream_id;
}

// ========== H2Session 実装 ==========

H2Session::H2Session(bool is_server, const H2SessionConfig& config)
    : is_server_(is_server), config_(config) {}

H2Session::~H2Session() {
  if (session_) {
    nghttp2_session_del(session_);
  }
}

H2Session::H2Session(H2Session&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      session_(other.session_),
      events_(std::move(other.events_)),
      send_buffer_(std::move(other.send_buffer_)),
      http2_stream_buffers_(std::move(other.http2_stream_buffers_)),
      pending_headers_(std::move(other.pending_headers_)),
      wt_sessions_(std::move(other.wt_sessions_)),
      closed_(other.closed_),
      goaway_sent_(other.goaway_sent_) {
  other.session_ = nullptr;
}

H2Session& H2Session::operator=(H2Session&& other) noexcept {
  if (this != &other) {
    if (session_) {
      nghttp2_session_del(session_);
    }
    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    session_ = other.session_;
    events_ = std::move(other.events_);
    send_buffer_ = std::move(other.send_buffer_);
    http2_stream_buffers_ = std::move(other.http2_stream_buffers_);
    pending_headers_ = std::move(other.pending_headers_);
    wt_sessions_ = std::move(other.wt_sessions_);
    closed_ = other.closed_;
    goaway_sent_ = other.goaway_sent_;
    other.session_ = nullptr;
  }
  return *this;
}

std::unique_ptr<H2Session> H2Session::create_client(
    const H2SessionConfig& config) {
  auto session = std::unique_ptr<H2Session>(new H2Session(false, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

std::unique_ptr<H2Session> H2Session::create_server(
    const H2SessionConfig& config) {
  auto session = std::unique_ptr<H2Session>(new H2Session(true, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

bool H2Session::initialize() {
  nghttp2_session_callbacks* callbacks;
  int rv = nghttp2_session_callbacks_new(&callbacks);
  if (rv != 0) {
    return false;
  }

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

  if (is_server_) {
    rv = nghttp2_session_server_new(&session_, callbacks, this);
  } else {
    rv = nghttp2_session_client_new(&session_, callbacks, this);
  }

  nghttp2_session_callbacks_del(callbacks);

  if (rv != 0) {
    return false;
  }

  // SETTINGS を送信
  nghttp2_settings_entry settings[] = {
      {NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS, config_.max_concurrent_streams},
      {NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE, config_.initial_window_size},
      {NGHTTP2_SETTINGS_MAX_FRAME_SIZE, config_.max_frame_size},
      {NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE, config_.max_header_list_size},
      {NGHTTP2_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1},
  };

  rv = nghttp2_submit_settings(session_, NGHTTP2_FLAG_NONE, settings,
                               sizeof(settings) / sizeof(settings[0]));
  if (rv != 0) {
    return false;
  }

  // クライアントの場合は connection preface を送信
  if (!is_server_) {
    nghttp2_session_send(session_);
  }

  return true;
}

size_t H2Session::receive(const std::vector<uint8_t>& data) {
  if (!session_) {
    return 0;
  }

  ssize_t processed =
      nghttp2_session_mem_recv(session_, data.data(), data.size());

  if (processed < 0) {
    H2Event event;
    event.type = H2EventType::Error;
    event.error_code = static_cast<uint32_t>(-processed);
    event.error_message = nghttp2_strerror(static_cast<int>(processed));
    push_event(std::move(event));
    return 0;
  }

  nghttp2_session_send(session_);

  return static_cast<size_t>(processed);
}

std::optional<std::vector<uint8_t>> H2Session::send() {
  if (!session_) {
    return std::nullopt;
  }

  nghttp2_session_send(session_);

  if (send_buffer_.empty()) {
    return std::nullopt;
  }

  std::vector<uint8_t> result;
  result.swap(send_buffer_);
  return result;
}

int32_t H2Session::connect(const std::string& url) {
  if (!session_ || is_server_) {
    return -1;
  }

  // URL をパース
  std::string authority;
  std::string path;

  size_t scheme_end = url.find("://");
  if (scheme_end != std::string::npos) {
    size_t host_start = scheme_end + 3;
    size_t path_start = url.find('/', host_start);
    if (path_start != std::string::npos) {
      authority = url.substr(host_start, path_start - host_start);
      path = url.substr(path_start);
    } else {
      authority = url.substr(host_start);
      path = "/";
    }
  } else {
    return -1;
  }

  // Extended CONNECT リクエストヘッダー
  std::string method = "CONNECT";
  std::string scheme = "https";
  std::string protocol = "webtransport";

  nghttp2_nv nva[] = {
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":method")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(method.c_str())),
       7, method.size(), NGHTTP2_NV_FLAG_NONE},
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":scheme")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(scheme.c_str())),
       7, scheme.size(), NGHTTP2_NV_FLAG_NONE},
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":authority")),
       const_cast<uint8_t*>(
           reinterpret_cast<const uint8_t*>(authority.c_str())),
       10, authority.size(), NGHTTP2_NV_FLAG_NONE},
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":path")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(path.c_str())), 5,
       path.size(), NGHTTP2_NV_FLAG_NONE},
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":protocol")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(protocol.c_str())),
       9, protocol.size(), NGHTTP2_NV_FLAG_NONE},
  };

  // Capsule データ送信用のデータプロバイダー
  nghttp2_data_provider data_prd;
  data_prd.source.ptr = this;
  data_prd.read_callback = data_source_read_callback;

  int32_t stream_id = nghttp2_submit_request(
      session_, nullptr, nva, sizeof(nva) / sizeof(nva[0]), &data_prd, this);

  if (stream_id < 0) {
    return -1;
  }

  // WebTransport セッションを作成
  WtSessionInfo wt_session;
  wt_session.http2_stream_id = stream_id;
  wt_session.max_data_local = config_.wt_initial_max_data;
  wt_session.max_data_remote = config_.wt_initial_max_data;
  wt_session.max_streams_bidi_local = config_.wt_initial_max_streams_bidi;
  wt_session.max_streams_uni_local = config_.wt_initial_max_streams_uni;
  wt_session.max_streams_bidi_remote = config_.wt_initial_max_streams_bidi;
  wt_session.max_streams_uni_remote = config_.wt_initial_max_streams_uni;
  wt_sessions_[stream_id] = wt_session;

  nghttp2_session_send(session_);

  return stream_id;
}

bool H2Session::accept_session(int32_t session_id) {
  if (!session_ || !is_server_) {
    return false;
  }

  // 200 OK レスポンス
  std::string status = "200";

  nghttp2_nv nva[] = {
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":status")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(status.c_str())),
       7, status.size(), NGHTTP2_NV_FLAG_NONE},
  };

  // Capsule データ送信用のデータプロバイダー
  nghttp2_data_provider data_prd;
  data_prd.source.ptr = this;
  data_prd.read_callback = data_source_read_callback;

  int rv = nghttp2_submit_response(session_, session_id, nva,
                                   sizeof(nva) / sizeof(nva[0]), &data_prd);
  if (rv != 0) {
    return false;
  }

  // WebTransport セッションを確立済みにする
  auto* wt_session = get_wt_session(session_id);
  if (wt_session) {
    wt_session->is_established = true;
  }

  nghttp2_session_send(session_);

  // 初期フロー制御 Capsule を送信
  std::vector<uint8_t> max_data_payload =
      encode_varint(config_.wt_initial_max_data);
  send_capsule(session_id, CapsuleType::WtMaxData, max_data_payload);

  std::vector<uint8_t> max_streams_bidi_payload =
      encode_varint(config_.wt_initial_max_streams_bidi);
  send_capsule(session_id, CapsuleType::WtMaxStreamsBidi,
               max_streams_bidi_payload);

  std::vector<uint8_t> max_streams_uni_payload =
      encode_varint(config_.wt_initial_max_streams_uni);
  send_capsule(session_id, CapsuleType::WtMaxStreamsUni,
               max_streams_uni_payload);

  return true;
}

void H2Session::reject_session(int32_t session_id, int status_code) {
  if (!session_ || !is_server_) {
    return;
  }

  std::string status = std::to_string(status_code);

  nghttp2_nv nva[] = {
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":status")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(status.c_str())),
       7, status.size(), NGHTTP2_NV_FLAG_NONE},
  };

  nghttp2_submit_response(session_, session_id, nva,
                          sizeof(nva) / sizeof(nva[0]), nullptr);
  wt_sessions_.erase(session_id);
  nghttp2_session_send(session_);
}

int64_t H2Session::open_stream(int32_t session_id, bool is_unidirectional) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || !wt_session->is_established) {
    return -1;
  }

  // ストリーム数制限チェック
  if (is_unidirectional) {
    if (wt_session->streams_uni_opened >= wt_session->max_streams_uni_local) {
      return -1;
    }
  } else {
    if (wt_session->streams_bidi_opened >= wt_session->max_streams_bidi_local) {
      return -1;
    }
  }

  // ストリーム ID を割り当て
  uint64_t stream_id = allocate_stream_id(session_id, is_unidirectional);
  if (stream_id == UINT64_MAX) {
    return -1;
  }

  // ストリーム情報を作成
  WtStreamInfo info;
  info.stream_id = stream_id;
  info.is_local = true;
  info.is_unidirectional = is_unidirectional;
  info.max_stream_data_local = config_.wt_initial_max_stream_data;
  info.max_stream_data_remote = config_.wt_initial_max_stream_data;
  wt_session->streams[stream_id] = info;

  // 空の WT_STREAM capsule を送信してストリームを開始
  std::vector<uint8_t> payload = encode_varint(stream_id);
  send_capsule(session_id, CapsuleType::WtStream, payload);

  return static_cast<int64_t>(stream_id);
}

void H2Session::send_stream_data(int32_t session_id,
                                 uint64_t stream_id,
                                 const std::vector<uint8_t>& data,
                                 bool fin) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // ストリームが存在しない場合はエラー
  if (wt_session->streams.find(stream_id) == wt_session->streams.end()) {
    return;
  }

  // WT_STREAM capsule ペイロード: Stream ID + Data
  std::vector<uint8_t> payload = encode_varint(stream_id);
  payload.insert(payload.end(), data.begin(), data.end());

  CapsuleType type = fin ? CapsuleType::WtStreamFin : CapsuleType::WtStream;
  send_capsule(session_id, type, payload);

  // フロー制御更新
  auto& stream_info = wt_session->streams[stream_id];
  stream_info.bytes_sent += data.size();
  wt_session->bytes_sent += data.size();
}

void H2Session::reset_stream(int32_t session_id,
                             uint64_t stream_id,
                             uint32_t error_code,
                             uint64_t reliable_size) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // WT_RESET_STREAM capsule: Stream ID + Error Code + Reliable Size
  std::vector<uint8_t> payload;
  auto stream_id_bytes = encode_varint(stream_id);
  auto error_code_bytes = encode_varint(error_code);
  auto reliable_size_bytes = encode_varint(reliable_size);

  payload.insert(payload.end(), stream_id_bytes.begin(), stream_id_bytes.end());
  payload.insert(payload.end(), error_code_bytes.begin(),
                 error_code_bytes.end());
  payload.insert(payload.end(), reliable_size_bytes.begin(),
                 reliable_size_bytes.end());

  send_capsule(session_id, CapsuleType::WtResetStream, payload);

  wt_session->streams.erase(stream_id);
}

void H2Session::stop_sending(int32_t session_id,
                             uint64_t stream_id,
                             uint32_t error_code) {
  // WT_STOP_SENDING capsule: Stream ID + Error Code
  std::vector<uint8_t> payload;
  auto stream_id_bytes = encode_varint(stream_id);
  auto error_code_bytes = encode_varint(error_code);

  payload.insert(payload.end(), stream_id_bytes.begin(), stream_id_bytes.end());
  payload.insert(payload.end(), error_code_bytes.begin(),
                 error_code_bytes.end());

  send_capsule(session_id, CapsuleType::WtStopSending, payload);
}

void H2Session::send_datagram(int32_t session_id,
                              const std::vector<uint8_t>& data) {
  send_capsule(session_id, CapsuleType::Datagram, data);
}

void H2Session::close_session(int32_t session_id,
                              uint32_t error_code,
                              const std::string& error_message) {
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // WT_CLOSE_SESSION capsule: Error Code (32bit) + Message
  std::vector<uint8_t> payload;
  payload.push_back(static_cast<uint8_t>((error_code >> 24) & 0xFF));
  payload.push_back(static_cast<uint8_t>((error_code >> 16) & 0xFF));
  payload.push_back(static_cast<uint8_t>((error_code >> 8) & 0xFF));
  payload.push_back(static_cast<uint8_t>(error_code & 0xFF));

  if (!error_message.empty()) {
    payload.insert(payload.end(), error_message.begin(), error_message.end());
  }

  send_capsule(session_id, CapsuleType::WtCloseSession, payload);

  // HTTP/2 ストリームを終了
  nghttp2_submit_rst_stream(session_, NGHTTP2_FLAG_NONE, session_id,
                            NGHTTP2_NO_ERROR);
  wt_sessions_.erase(session_id);
  nghttp2_session_send(session_);
}

void H2Session::drain_session(int32_t session_id) {
  send_capsule(session_id, CapsuleType::WtDrainSession, {});
}

std::optional<H2Event> H2Session::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  H2Event event = std::move(events_.front());
  events_.pop_front();
  return event;
}

bool H2Session::want_write() const {
  if (!session_) {
    return false;
  }
  return nghttp2_session_want_write(session_) != 0 || !send_buffer_.empty();
}

bool H2Session::is_closed() const {
  return closed_;
}

std::vector<int32_t> H2Session::get_session_ids() const {
  std::vector<int32_t> result;
  for (const auto& pair : wt_sessions_) {
    if (pair.second.is_established) {
      result.push_back(pair.first);
    }
  }
  return result;
}

std::vector<uint64_t> H2Session::get_stream_ids(int32_t session_id) const {
  std::vector<uint64_t> result;
  auto it = wt_sessions_.find(session_id);
  if (it != wt_sessions_.end()) {
    for (const auto& stream_pair : it->second.streams) {
      result.push_back(stream_pair.first);
    }
  }
  return result;
}

void H2Session::push_event(H2Event event) {
  events_.push_back(std::move(event));
}

WtSessionInfo* H2Session::get_wt_session(int32_t session_id) {
  auto it = wt_sessions_.find(session_id);
  if (it != wt_sessions_.end()) {
    return &it->second;
  }
  return nullptr;
}

// ========== nghttp2 コールバック実装 ==========

ssize_t H2Session::send_callback(nghttp2_session* session,
                                 const uint8_t* data,
                                 size_t length,
                                 int flags,
                                 void* user_data) {
  (void)session;
  (void)flags;

  auto* h2_session = static_cast<H2Session*>(user_data);
  h2_session->send_buffer_.insert(h2_session->send_buffer_.end(), data,
                                  data + length);
  return static_cast<ssize_t>(length);
}

int H2Session::on_frame_recv_callback(nghttp2_session* session,
                                      const nghttp2_frame* frame,
                                      void* user_data) {
  (void)session;

  auto* h2_session = static_cast<H2Session*>(user_data);
  int32_t stream_id = frame->hd.stream_id;

  switch (frame->hd.type) {
    case NGHTTP2_HEADERS:
      if (frame->headers.cat == NGHTTP2_HCAT_REQUEST &&
          h2_session->is_server_) {
        // サーバー側でリクエストを受信
        auto it = h2_session->pending_headers_.find(stream_id);
        if (it != h2_session->pending_headers_.end()) {
          // WebTransport CONNECT リクエストかチェック
          bool is_connect = false;
          bool is_webtransport = false;
          for (const auto& [name, value] : it->second) {
            if (name == ":method" && value == "CONNECT") {
              is_connect = true;
            }
            if (name == ":protocol" && value == "webtransport") {
              is_webtransport = true;
            }
          }
          if (is_connect && is_webtransport) {
            // WebTransport セッション情報を作成
            WtSessionInfo wt_session;
            wt_session.http2_stream_id = stream_id;
            wt_session.max_data_local = h2_session->config_.wt_initial_max_data;
            wt_session.max_data_remote =
                h2_session->config_.wt_initial_max_data;
            wt_session.max_streams_bidi_local =
                h2_session->config_.wt_initial_max_streams_bidi;
            wt_session.max_streams_uni_local =
                h2_session->config_.wt_initial_max_streams_uni;
            wt_session.max_streams_bidi_remote =
                h2_session->config_.wt_initial_max_streams_bidi;
            wt_session.max_streams_uni_remote =
                h2_session->config_.wt_initial_max_streams_uni;
            h2_session->wt_sessions_[stream_id] = wt_session;

            // WebTransport セッションリクエスト
            H2Event event;
            event.type = H2EventType::SessionReady;
            event.session_id = stream_id;
            h2_session->push_event(std::move(event));
          }
          h2_session->pending_headers_.erase(it);
        }
      } else if (frame->headers.cat == NGHTTP2_HCAT_RESPONSE &&
                 !h2_session->is_server_) {
        // クライアント側でレスポンスを受信
        auto it = h2_session->pending_headers_.find(stream_id);
        if (it != h2_session->pending_headers_.end()) {
          // 200 レスポンスかチェック
          bool is_success = false;
          for (const auto& [name, value] : it->second) {
            if (name == ":status" && value == "200") {
              is_success = true;
              break;
            }
          }
          auto* wt_session = h2_session->get_wt_session(stream_id);
          if (is_success && wt_session) {
            wt_session->is_established = true;

            // WebTransport セッション確立
            H2Event event;
            event.type = H2EventType::SessionReady;
            event.session_id = stream_id;
            h2_session->push_event(std::move(event));

            // 初期フロー制御 Capsule を送信
            std::vector<uint8_t> max_data_payload = h2_session->encode_varint(
                h2_session->config_.wt_initial_max_data);
            h2_session->send_capsule(stream_id, CapsuleType::WtMaxData,
                                     max_data_payload);

            std::vector<uint8_t> max_streams_bidi_payload =
                h2_session->encode_varint(
                    h2_session->config_.wt_initial_max_streams_bidi);
            h2_session->send_capsule(stream_id, CapsuleType::WtMaxStreamsBidi,
                                     max_streams_bidi_payload);

            std::vector<uint8_t> max_streams_uni_payload =
                h2_session->encode_varint(
                    h2_session->config_.wt_initial_max_streams_uni);
            h2_session->send_capsule(stream_id, CapsuleType::WtMaxStreamsUni,
                                     max_streams_uni_payload);
          }
          h2_session->pending_headers_.erase(it);
        }
      }
      break;

    case NGHTTP2_GOAWAY:
      h2_session->closed_ = true;
      break;

    default:
      break;
  }

  return 0;
}

int H2Session::on_data_chunk_recv_callback(nghttp2_session* session,
                                           uint8_t flags,
                                           int32_t stream_id,
                                           const uint8_t* data,
                                           size_t len,
                                           void* user_data) {
  (void)session;
  (void)flags;

  auto* h2_session = static_cast<H2Session*>(user_data);

  // WebTransport セッションのデータとして Capsule を処理
  auto* wt_session = h2_session->get_wt_session(stream_id);
  if (wt_session && wt_session->is_established) {
    h2_session->process_capsules(stream_id, data, len);
  }

  return 0;
}

int H2Session::on_stream_close_callback(nghttp2_session* session,
                                        int32_t stream_id,
                                        uint32_t error_code,
                                        void* user_data) {
  (void)session;

  auto* h2_session = static_cast<H2Session*>(user_data);

  // WebTransport セッションが閉じられた場合
  auto* wt_session = h2_session->get_wt_session(stream_id);
  if (wt_session) {
    H2Event event;
    event.type = H2EventType::SessionClosed;
    event.session_id = stream_id;
    event.error_code = error_code;
    h2_session->push_event(std::move(event));
    h2_session->wt_sessions_.erase(stream_id);
  }

  h2_session->http2_stream_buffers_.erase(stream_id);
  return 0;
}

int H2Session::on_header_callback(nghttp2_session* session,
                                  const nghttp2_frame* frame,
                                  const uint8_t* name,
                                  size_t namelen,
                                  const uint8_t* value,
                                  size_t valuelen,
                                  uint8_t flags,
                                  void* user_data) {
  (void)session;
  (void)flags;

  auto* h2_session = static_cast<H2Session*>(user_data);
  int32_t stream_id = frame->hd.stream_id;

  std::string header_name(reinterpret_cast<const char*>(name), namelen);
  std::string header_value(reinterpret_cast<const char*>(value), valuelen);

  h2_session->pending_headers_[stream_id].emplace_back(std::move(header_name),
                                                       std::move(header_value));
  return 0;
}

int H2Session::on_begin_headers_callback(nghttp2_session* session,
                                         const nghttp2_frame* frame,
                                         void* user_data) {
  (void)session;

  auto* h2_session = static_cast<H2Session*>(user_data);
  int32_t stream_id = frame->hd.stream_id;

  h2_session->pending_headers_[stream_id].clear();
  return 0;
}

ssize_t H2Session::data_source_read_callback(nghttp2_session* session,
                                             int32_t stream_id,
                                             uint8_t* buf,
                                             size_t length,
                                             uint32_t* data_flags,
                                             nghttp2_data_source* source,
                                             void* user_data) {
  (void)session;
  (void)source;

  auto* h2_session = static_cast<H2Session*>(user_data);

  auto it = h2_session->http2_stream_buffers_.find(stream_id);
  if (it == h2_session->http2_stream_buffers_.end() || it->second.empty()) {
    return NGHTTP2_ERR_DEFERRED;
  }

  auto& buffer = it->second.front();
  size_t to_read = std::min(length, buffer.size());

  if (to_read > 0) {
    std::memcpy(buf, buffer.data(), to_read);
    buffer.erase(buffer.begin(),
                 buffer.begin() + static_cast<std::ptrdiff_t>(to_read));
  }

  if (buffer.empty()) {
    it->second.pop_front();
  }

  // バッファが空になっても EOF フラグは設定しない
  // WebTransport セッションは開いたままなので、次のデータまで待機する
  // 次回コールバックが呼ばれたときに DEFERRED を返す
  (void)data_flags;

  return static_cast<ssize_t>(to_read);
}

// ========== Python バインディング ==========

void bind_webtransport_h2(nb::module_& m) {
  auto h2_mod = m.def_submodule("h2", "WebTransport over HTTP/2");

  // CapsuleType
  nb::enum_<CapsuleType>(h2_mod, "CapsuleType", "Capsule 種別")
      .value("DATAGRAM", CapsuleType::Datagram)
      .value("PADDING", CapsuleType::Padding)
      .value("WT_RESET_STREAM", CapsuleType::WtResetStream)
      .value("WT_STOP_SENDING", CapsuleType::WtStopSending)
      .value("WT_STREAM", CapsuleType::WtStream)
      .value("WT_STREAM_FIN", CapsuleType::WtStreamFin)
      .value("WT_MAX_DATA", CapsuleType::WtMaxData)
      .value("WT_MAX_STREAM_DATA", CapsuleType::WtMaxStreamData)
      .value("WT_MAX_STREAMS_BIDI", CapsuleType::WtMaxStreamsBidi)
      .value("WT_MAX_STREAMS_UNI", CapsuleType::WtMaxStreamsUni)
      .value("WT_DATA_BLOCKED", CapsuleType::WtDataBlocked)
      .value("WT_STREAM_DATA_BLOCKED", CapsuleType::WtStreamDataBlocked)
      .value("WT_STREAMS_BLOCKED_BIDI", CapsuleType::WtStreamsBlockedBidi)
      .value("WT_STREAMS_BLOCKED_UNI", CapsuleType::WtStreamsBlockedUni)
      .value("WT_CLOSE_SESSION", CapsuleType::WtCloseSession)
      .value("WT_DRAIN_SESSION", CapsuleType::WtDrainSession);

  // H2SessionConfig
  nb::class_<H2SessionConfig>(h2_mod, "Config", "WebTransport over HTTP/2 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("initial_window_size", &H2SessionConfig::initial_window_size)
      .def_rw("max_concurrent_streams",
              &H2SessionConfig::max_concurrent_streams)
      .def_rw("max_frame_size", &H2SessionConfig::max_frame_size)
      .def_rw("max_header_list_size", &H2SessionConfig::max_header_list_size)
      .def_rw("is_server", &H2SessionConfig::is_server)
      .def_rw("wt_initial_max_data", &H2SessionConfig::wt_initial_max_data)
      .def_rw("wt_initial_max_stream_data",
              &H2SessionConfig::wt_initial_max_stream_data)
      .def_rw("wt_initial_max_streams_bidi",
              &H2SessionConfig::wt_initial_max_streams_bidi)
      .def_rw("wt_initial_max_streams_uni",
              &H2SessionConfig::wt_initial_max_streams_uni);

  // H2EventType
  nb::enum_<H2EventType>(h2_mod, "EventType",
                         "WebTransport over HTTP/2 イベント種別")
      .value("SESSION_READY", H2EventType::SessionReady)
      .value("SESSION_CLOSED", H2EventType::SessionClosed)
      .value("SESSION_DRAINING", H2EventType::SessionDraining)
      .value("STREAM_DATA", H2EventType::StreamData)
      .value("STREAM_RESET", H2EventType::StreamReset)
      .value("STOP_SENDING", H2EventType::StopSending)
      .value("DATAGRAM", H2EventType::Datagram)
      .value("ERROR", H2EventType::Error);

  // H2Event
  nb::class_<H2Event>(h2_mod, "Event", "WebTransport over HTTP/2 イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &H2Event::type)
      .def_ro("session_id", &H2Event::session_id)
      .def_ro("stream_id", &H2Event::stream_id)
      .def_prop_ro(
          "data",
          [](const H2Event& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "イベントデータ")
      .def_ro("error_code", &H2Event::error_code)
      .def_ro("error_message", &H2Event::error_message)
      .def_ro("fin", &H2Event::fin);

  // H2Session
  nb::class_<H2Session>(h2_mod, "Session",
                        "WebTransport over HTTP/2 セッション")
      .def_static(
          "create_client",
          [](const H2SessionConfig& config) {
            auto session = H2Session::create_client(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H2 client session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Session"),
          "クライアントセッションを作成")
      .def_static(
          "create_server",
          [](const H2SessionConfig& config) {
            auto session = H2Session::create_server(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H2 server session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Session"),
          "サーバーセッションを作成")
      .def(
          "receive",
          [](H2Session& s, nb::bytes data) {
            return s.receive(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"), nb::sig("def receive(self, data: bytes) -> int"),
          "受信したデータを処理")
      .def(
          "send",
          [](H2Session& s) -> std::optional<nb::bytes> {
            auto data = s.send();
            if (!data) {
              return std::nullopt;
            }
            return nb::bytes(reinterpret_cast<const char*>(data->data()),
                             data->size());
          },
          nb::sig("def send(self) -> bytes | None"), "送信すべきデータを取得")
      .def("connect", &H2Session::connect, nb::arg("url"),
           nb::sig("def connect(self, url: str) -> int"),
           "WebTransport セッションを開始 (クライアント用)")
      .def("accept_session", &H2Session::accept_session, nb::arg("session_id"),
           nb::sig("def accept_session(self, session_id: int) -> bool"),
           "WebTransport セッションを受理 (サーバー用)")
      .def("reject_session", &H2Session::reject_session, nb::arg("session_id"),
           nb::arg("status_code"),
           nb::sig("def reject_session(self, session_id: int, status_code: "
                   "int) -> None"),
           "WebTransport セッションを拒否 (サーバー用)")
      .def("open_stream", &H2Session::open_stream, nb::arg("session_id"),
           nb::arg("is_unidirectional"),
           nb::sig("def open_stream(self, session_id: int, is_unidirectional: "
                   "bool) -> int"),
           "WebTransport ストリームを開く")
      .def(
          "send_stream_data",
          [](H2Session& s, int32_t session_id, uint64_t stream_id,
             nb::bytes data, bool fin) {
            s.send_stream_data(
                session_id, stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("session_id"), nb::arg("stream_id"), nb::arg("data"),
          nb::arg("fin") = false,
          nb::sig("def send_stream_data(self, session_id: int, stream_id: int, "
                  "data: bytes, fin: bool = False) -> None"),
          "WebTransport ストリームにデータを送信")
      .def("reset_stream", &H2Session::reset_stream, nb::arg("session_id"),
           nb::arg("stream_id"), nb::arg("error_code"),
           nb::arg("reliable_size") = 0,
           nb::sig("def reset_stream(self, session_id: int, stream_id: int, "
                   "error_code: int, reliable_size: int = 0) -> None"),
           "WebTransport ストリームをリセット")
      .def("stop_sending", &H2Session::stop_sending, nb::arg("session_id"),
           nb::arg("stream_id"), nb::arg("error_code"),
           nb::sig("def stop_sending(self, session_id: int, stream_id: int, "
                   "error_code: int) -> None"),
           "送信停止を要求")
      .def(
          "send_datagram",
          [](H2Session& s, int32_t session_id, nb::bytes data) {
            s.send_datagram(
                session_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("session_id"), nb::arg("data"),
          nb::sig(
              "def send_datagram(self, session_id: int, data: bytes) -> None"),
          "データグラムを送信")
      .def("close_session", &H2Session::close_session, nb::arg("session_id"),
           nb::arg("error_code") = 0, nb::arg("error_message") = "",
           nb::sig("def close_session(self, session_id: int, error_code: int = "
                   "0, error_message: str = '') -> None"),
           "WebTransport セッションを閉じる")
      .def("drain_session", &H2Session::drain_session, nb::arg("session_id"),
           nb::sig("def drain_session(self, session_id: int) -> None"),
           "セッションのドレインを開始")
      .def("next_event", &H2Session::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("want_write", &H2Session::want_write,
           nb::sig("def want_write(self) -> bool"), "送信待ちデータがあるか")
      .def("is_closed", &H2Session::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def("get_session_ids", &H2Session::get_session_ids,
           nb::sig("def get_session_ids(self) -> list[int]"),
           "確立されたセッション ID のリストを取得")
      .def("get_stream_ids", &H2Session::get_stream_ids, nb::arg("session_id"),
           nb::sig("def get_stream_ids(self, session_id: int) -> list[int]"),
           "セッションに属するストリーム ID を取得");
}

}  // namespace h2
}  // namespace webtransport
