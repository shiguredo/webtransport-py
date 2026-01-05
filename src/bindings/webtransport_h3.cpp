/**
 * WebTransport over HTTP/3 バインディング実装
 */

#include "webtransport_h3.h"

#include <cstddef>
#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace h3 {

// ========== H3Session 実装 ==========

H3Session::H3Session(bool is_server, const H3SessionConfig& config)
    : is_server_(is_server), config_(config) {}

H3Session::~H3Session() {
  if (conn_) {
    nghttp3_conn_del(conn_);
  }
}

H3Session::H3Session(H3Session&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      conn_(other.conn_),
      events_(std::move(other.events_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      pending_sends_(std::move(other.pending_sends_)),
      pending_datagrams_(std::move(other.pending_datagrams_)),
      pending_headers_(std::move(other.pending_headers_)),
      session_ids_(std::move(other.session_ids_)),
      stream_info_(std::move(other.stream_info_)),
      control_stream_id_(other.control_stream_id_),
      qpack_encoder_stream_id_(other.qpack_encoder_stream_id_),
      qpack_decoder_stream_id_(other.qpack_decoder_stream_id_),
      closed_(other.closed_) {
  other.conn_ = nullptr;
}

H3Session& H3Session::operator=(H3Session&& other) noexcept {
  if (this != &other) {
    if (conn_) {
      nghttp3_conn_del(conn_);
    }
    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    conn_ = other.conn_;
    events_ = std::move(other.events_);
    stream_buffers_ = std::move(other.stream_buffers_);
    pending_sends_ = std::move(other.pending_sends_);
    pending_datagrams_ = std::move(other.pending_datagrams_);
    pending_headers_ = std::move(other.pending_headers_);
    session_ids_ = std::move(other.session_ids_);
    stream_info_ = std::move(other.stream_info_);
    control_stream_id_ = other.control_stream_id_;
    qpack_encoder_stream_id_ = other.qpack_encoder_stream_id_;
    qpack_decoder_stream_id_ = other.qpack_decoder_stream_id_;
    closed_ = other.closed_;
    other.conn_ = nullptr;
  }
  return *this;
}

std::unique_ptr<H3Session> H3Session::create_client(
    const H3SessionConfig& config) {
  auto session = std::unique_ptr<H3Session>(new H3Session(false, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

std::unique_ptr<H3Session> H3Session::create_server(
    const H3SessionConfig& config) {
  auto session = std::unique_ptr<H3Session>(new H3Session(true, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

bool H3Session::initialize() {
  nghttp3_callbacks callbacks{};
  callbacks.acked_stream_data = acked_stream_data_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.recv_data = recv_data_cb;
  callbacks.deferred_consume = deferred_consume_cb;
  callbacks.begin_headers = begin_headers_cb;
  callbacks.recv_header = recv_header_cb;
  callbacks.end_headers = end_headers_cb;
  callbacks.stop_sending = stop_sending_cb;
  callbacks.reset_stream = reset_stream_cb;
  callbacks.shutdown = shutdown_cb;
  callbacks.recv_settings2 = recv_settings2_cb;
  callbacks.recv_wt_data = recv_wt_data_cb;

  nghttp3_settings settings;
  nghttp3_settings_default(&settings);
  settings.max_field_section_size = config_.max_field_section_size;
  settings.qpack_max_dtable_capacity = config_.qpack_max_dtable_capacity;
  settings.qpack_blocked_streams = config_.qpack_blocked_streams;

  // WebTransport を有効化
  settings.enable_connect_protocol = 1;
  settings.h3_datagram = 1;
  settings.wt_max_sessions = 1;

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

size_t H3Session::receive_stream_data(int64_t stream_id,
                                      const std::vector<uint8_t>& data,
                                      bool fin) {
  if (!conn_) {
    return 0;
  }

  // タイムスタンプを 0 にして read_stream2 を使用
  // nghttp3 は WebTransport データストリームのヘッダを自動的にパースし、
  // recv_wt_data コールバックを呼び出す
  nghttp3_ssize consumed = nghttp3_conn_read_stream2(
      conn_, stream_id, data.data(), data.size(), fin ? 1 : 0, 0);

  if (consumed < 0) {
    H3Event event;
    event.type = H3EventType::Error;
    event.stream_id = stream_id;
    event.error_code = static_cast<uint64_t>(-consumed);
    event.error_message = nghttp3_strerror(static_cast<int>(consumed));
    push_event(std::move(event));
    return 0;
  }

  return static_cast<size_t>(consumed);
}

void H3Session::receive_datagram(const std::vector<uint8_t>& data) {
  // WebTransport データグラムを処理
  // quarter stream ID をデコードしてセッションを特定
  if (data.size() < 1) {
    return;
  }

  // 簡易的なデータグラム処理
  // 実際には quarter stream ID のデコードが必要
  H3Event event;
  event.type = H3EventType::Datagram;
  event.data = data;
  push_event(std::move(event));
}

std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>>
H3Session::get_streams_to_send() {
  std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>> result;

  if (!conn_) {
    return result;
  }

  // nghttp3 からデータを読み出す
  for (;;) {
    nghttp3_vec vec[8];
    int64_t stream_id = -1;
    int fin = 0;

    nghttp3_ssize sveccnt =
        nghttp3_conn_writev_stream(conn_, &stream_id, &fin, vec, 8);

    if (sveccnt < 0) {
      break;
    }

    if (stream_id < 0) {
      break;
    }

    // データをコピー
    std::vector<uint8_t> data;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      data.insert(data.end(), vec[i].base, vec[i].base + vec[i].len);
    }

    if (!data.empty() || fin) {
      result.emplace_back(stream_id, std::move(data), fin != 0);
    }

    // 送信済みを通知
    size_t total = 0;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      total += vec[i].len;
    }
    if (total > 0) {
      nghttp3_conn_add_write_offset(conn_, stream_id, total);
    }

    if (sveccnt == 0 && fin == 0) {
      break;
    }
  }

  return result;
}

std::vector<std::vector<uint8_t>> H3Session::get_datagrams_to_send() {
  std::vector<std::vector<uint8_t>> result(pending_datagrams_.begin(),
                                           pending_datagrams_.end());
  pending_datagrams_.clear();
  return result;
}

void H3Session::bind_control_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  // QUIC varint の最大値チェック
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  control_stream_id_ = stream_id;
  nghttp3_conn_bind_control_stream(conn_, stream_id);
}

void H3Session::bind_qpack_encoder_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  qpack_encoder_stream_id_ = stream_id;
  // 両方のストリーム ID が有効な場合のみバインドする
  if (qpack_decoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

void H3Session::bind_qpack_decoder_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  qpack_decoder_stream_id_ = stream_id;
  if (qpack_encoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

bool H3Session::connect(int64_t stream_id, const std::string& url) {
  if (!conn_ || is_server_) {
    return false;
  }
  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }
  // ストリーム ID の検証
  // クライアント起動の双方向ストリームである必要がある (stream_id % 4 == 0)
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint || stream_id % 4 != 0) {
    return false;
  }

  // URL をパース
  std::string authority;
  std::string path;

  // 簡易的な URL パース
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
    return false;
  }

  // WebTransport CONNECT リクエストヘッダー
  // ヘッダー名は静的文字列リテラルを使用
  static const char* header_method = ":method";
  static const char* header_scheme = ":scheme";
  static const char* header_authority = ":authority";
  static const char* header_path = ":path";
  static const char* header_protocol = ":protocol";

  std::string method = "CONNECT";
  std::string scheme = "https";
  std::string protocol = "webtransport";

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_method)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(method.data())),
       strlen(header_method), method.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_scheme)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(scheme.data())),
       strlen(header_scheme), scheme.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_authority)),
       reinterpret_cast<uint8_t*>(authority.data()), strlen(header_authority),
       authority.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_path)),
       reinterpret_cast<uint8_t*>(path.data()), strlen(header_path),
       path.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_protocol)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(protocol.data())),
       strlen(header_protocol), protocol.size(), NGHTTP3_NV_FLAG_NONE},
  };

  // WebTransport 専用の submit 関数を使用
  int rv = nghttp3_conn_submit_wt_request(conn_, stream_id, nva.data(),
                                          nva.size(), nullptr);
  if (rv != 0) {
    return false;
  }

  // セッション ID を記録
  session_ids_.insert(stream_id);

  return true;
}

bool H3Session::accept_session(int64_t stream_id) {
  if (!conn_ || !is_server_) {
    return false;
  }
  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }

  // 200 OK レスポンス
  // ヘッダー名と値は静的文字列リテラルを使用
  static const char* header_status = ":status";
  static const char* value_status = "200";
  static const char* header_draft = "sec-webtransport-http3-draft";
  static const char* value_draft = "draft02";

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_status)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(value_status)),
       strlen(header_status), strlen(value_status), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_draft)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(value_draft)),
       strlen(header_draft), strlen(value_draft), NGHTTP3_NV_FLAG_NONE},
  };

  // WebTransport セッション用のレスポンスを送信
  // nghttp3_conn_submit_wt_response を使用 (end_headers コールバック外なので)
  int rv =
      nghttp3_conn_submit_wt_response(conn_, stream_id, nva.data(), nva.size());
  if (rv != 0) {
    return false;
  }

  // WebTransport セッションを確認済みとしてマーク
  // end_headers コールバック外で submit_wt_response を呼んだ場合は必須
  rv = nghttp3_conn_server_confirm_wt_session(conn_, stream_id, UINT64_MAX);
  if (rv != 0) {
    return false;
  }

  // セッション ID を記録
  session_ids_.insert(stream_id);

  return true;
}

void H3Session::reject_session(int64_t stream_id, int status_code) {
  if (!conn_ || !is_server_) {
    return;
  }
  // QPACK ストリームがバインドされていない場合は何もしない
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return;
  }

  // ヘッダー名は静的文字列リテラルを使用
  static const char* header_status = ":status";
  // ステータスコード値は submit_response 呼び出し中有効である必要がある
  std::string status_value = std::to_string(status_code);

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_status)),
       reinterpret_cast<uint8_t*>(status_value.data()), strlen(header_status),
       status_value.size(), NGHTTP3_NV_FLAG_NONE},
  };

  nghttp3_conn_submit_response(conn_, stream_id, nva.data(), nva.size(),
                               nullptr);
}

// WebTransport データストリーム用の read_data コールバック
// stream_buffers_ からデータを取得して返す
static nghttp3_ssize wt_data_read_callback(nghttp3_conn* /*conn*/,
                                           int64_t stream_id,
                                           nghttp3_vec* vec,
                                           size_t veccnt,
                                           uint32_t* pflags,
                                           void* conn_user_data,
                                           void* /*stream_user_data*/) {
  if (veccnt == 0 || !conn_user_data) {
    return NGHTTP3_ERR_WOULDBLOCK;
  }

  auto* session = static_cast<H3Session*>(conn_user_data);
  return session->read_data_callback(stream_id, vec, veccnt, pflags);
}

bool H3Session::open_stream(int64_t session_id,
                            int64_t stream_id,
                            bool is_unidirectional) {
  if (!conn_) {
    return false;
  }

  // QUIC varint の最大値チェック
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return false;
  }
  if (session_id < 0 || session_id > max_varint) {
    return false;
  }

  // nghttp3 はストリームタイプをアサーションでチェックする
  // クライアント: client_bidi (%4==0) or server_bidi (%4==1) or client_uni
  // (%4==2)
  // サーバー: client_bidi (%4==0) or server_bidi (%4==1) or server_uni (%4==3)
  int mod = stream_id % 4;
  if (is_server_) {
    // サーバーは client_bidi, server_bidi, server_uni のみ許可
    if (mod != 0 && mod != 1 && mod != 3) {
      return false;
    }
  } else {
    // クライアントは client_bidi, server_bidi, client_uni のみ許可
    if (mod != 0 && mod != 1 && mod != 2) {
      return false;
    }
  }

  // nghttp3 に WebTransport データストリームを登録
  // nghttp3 が WT ヘッダ (ストリームタイプ + セッション ID) を出力する
  nghttp3_data_reader dr;
  dr.read_data = wt_data_read_callback;

  int rv = nghttp3_conn_open_wt_data_stream(conn_, session_id, stream_id, &dr,
                                            nullptr);
  if (rv != 0) {
    return false;
  }

  // ストリーム情報を記録
  StreamInfo info;
  info.stream_id = stream_id;
  info.session_id = session_id;
  info.is_unidirectional = is_unidirectional;
  info.is_incoming = false;
  info.is_write_registered = true;

  stream_info_[stream_id] = info;

  return true;
}

void H3Session::send_stream_data(int64_t stream_id,
                                 const std::vector<uint8_t>& data,
                                 bool fin) {
  if (!conn_) {
    return;
  }

  // まだ nghttp3 に書き込み用として登録されていない場合は登録
  // リモート起動のストリームは recv_wt_data_cb で stream_info_ に追加されるが
  // 書き込み登録はされていないため、is_write_registered で判定する
  auto it = stream_info_.find(stream_id);
  bool needs_registration =
      (it == stream_info_.end()) || !it->second.is_write_registered;

  if (needs_registration) {
    // セッション ID を見つける
    int64_t session_id = -1;
    if (it != stream_info_.end()) {
      // 既に stream_info_ にある場合はそのセッション ID を使用
      session_id = it->second.session_id;
    } else if (!session_ids_.empty()) {
      // なければ最初のセッションを使用
      session_id = *session_ids_.begin();
    }
    if (session_id >= 0) {
      nghttp3_data_reader dr;
      dr.read_data = wt_data_read_callback;

      int rv = nghttp3_conn_open_wt_data_stream(conn_, session_id, stream_id,
                                                &dr, nullptr);
      if (rv == 0) {
        if (it != stream_info_.end()) {
          // 既存エントリの is_write_registered を更新
          it->second.is_write_registered = true;
        } else {
          // 新規エントリを作成
          StreamInfo info;
          info.stream_id = stream_id;
          info.session_id = session_id;
          info.is_unidirectional = false;
          info.is_incoming = true;
          info.is_write_registered = true;
          stream_info_[stream_id] = info;
        }
      }
    }
  }

  // ストリームバッファに追加
  StreamBuffer buf;
  buf.data = data;
  buf.fin = fin;
  stream_buffers_[stream_id].push_back(std::move(buf));

  // nghttp3 にデータが利用可能になったことを通知
  // これにより read_data コールバックが呼ばれるようになる
  nghttp3_conn_resume_stream(conn_, stream_id);
}

nghttp3_ssize H3Session::read_data_callback(int64_t stream_id,
                                            nghttp3_vec* vec,
                                            size_t veccnt,
                                            uint32_t* pflags) {
  (void)veccnt;

  auto it = stream_buffers_.find(stream_id);
  if (it == stream_buffers_.end() || it->second.empty()) {
    return NGHTTP3_ERR_WOULDBLOCK;
  }

  auto& buffers = it->second;
  auto& front = buffers.front();

  // 先頭のデータを返す
  vec[0].base = const_cast<uint8_t*>(front.data.data());
  vec[0].len = front.data.size();

  // FIN フラグの処理
  if (front.fin && buffers.size() == 1) {
    *pflags |= NGHTTP3_DATA_FLAG_EOF;
  }

  return 1;
}

void H3Session::send_datagram(int64_t session_id,
                              const std::vector<uint8_t>& data) {
  // Quarter stream ID をエンコード
  // session_id / 4 を可変長整数でエンコード
  std::vector<uint8_t> datagram;

  uint64_t quarter_stream_id = static_cast<uint64_t>(session_id) / 4;

  // 簡易的な可変長整数エンコード
  if (quarter_stream_id < 64) {
    datagram.push_back(static_cast<uint8_t>(quarter_stream_id));
  } else if (quarter_stream_id < 16384) {
    datagram.push_back(static_cast<uint8_t>(0x40 | (quarter_stream_id >> 8)));
    datagram.push_back(static_cast<uint8_t>(quarter_stream_id & 0xff));
  } else {
    // 4バイト以上は省略
    return;
  }

  datagram.insert(datagram.end(), data.begin(), data.end());
  pending_datagrams_.push_back(std::move(datagram));
}

void H3Session::close_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_) {
    return;
  }

  nghttp3_conn_close_stream(conn_, stream_id, error_code);
  stream_info_.erase(stream_id);
}

void H3Session::close_session(int64_t session_id,
                              uint64_t error_code,
                              const std::string& error_message) {
  if (!conn_) {
    return;
  }

  // nghttp3 の WebTransport セッション終了 API を使用
  // WT_CLOSE_SESSION カプセルを送信し、全ストリームを適切にシャットダウン
  int rv = nghttp3_conn_close_wt_session(
      conn_, session_id, static_cast<uint32_t>(error_code),
      reinterpret_cast<const uint8_t*>(error_message.data()),
      error_message.size());

  if (rv != 0) {
    return;
  }

  // ローカルのストリーム情報をクリーンアップ
  std::vector<int64_t> streams_to_remove;
  for (const auto& pair : stream_info_) {
    if (pair.second.session_id == session_id) {
      streams_to_remove.push_back(pair.first);
    }
  }
  for (int64_t stream_id : streams_to_remove) {
    stream_info_.erase(stream_id);
  }

  session_ids_.erase(session_id);

  // セッション終了イベント
  H3Event event;
  event.type = H3EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = error_code;
  event.error_message = error_message;
  push_event(std::move(event));
}

std::optional<H3Event> H3Session::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  H3Event event = std::move(events_.front());
  events_.pop_front();
  return event;
}

std::vector<std::pair<std::string, bool>> H3Session::get_required_streams()
    const {
  std::vector<std::pair<std::string, bool>> result;
  result.emplace_back("control", false);        // 単方向、送信
  result.emplace_back("qpack_encoder", false);  // 単方向、送信
  result.emplace_back("qpack_decoder", false);  // 単方向、送信
  return result;
}

bool H3Session::is_closed() const {
  return closed_;
}

std::vector<int64_t> H3Session::get_session_ids() const {
  return std::vector<int64_t>(session_ids_.begin(), session_ids_.end());
}

std::vector<StreamInfo> H3Session::get_session_streams(
    int64_t session_id) const {
  std::vector<StreamInfo> result;
  for (const auto& pair : stream_info_) {
    if (pair.second.session_id == session_id) {
      result.push_back(pair.second);
    }
  }
  return result;
}

void H3Session::set_max_client_streams_bidi(uint64_t max_streams) {
  if (!conn_) {
    return;
  }
  nghttp3_conn_set_max_client_streams_bidi(conn_, max_streams);
}

void H3Session::push_event(H3Event event) {
  events_.push_back(std::move(event));
}

// ========== nghttp3 コールバック実装 ==========

int H3Session::acked_stream_data_cb(nghttp3_conn* /*conn*/,
                                    int64_t stream_id,
                                    uint64_t datalen,
                                    void* conn_user_data,
                                    void* /*stream_user_data*/) {
  auto* session = static_cast<H3Session*>(conn_user_data);
  if (!session) {
    return 0;
  }

  // ACK されたデータを stream_buffers_ から削除
  auto it = session->stream_buffers_.find(stream_id);
  if (it == session->stream_buffers_.end()) {
    return 0;
  }

  auto& buffers = it->second;
  uint64_t remaining = datalen;

  while (remaining > 0 && !buffers.empty()) {
    auto& front = buffers.front();
    if (front.data.size() <= remaining) {
      remaining -= front.data.size();
      buffers.pop_front();
    } else {
      // 部分的に ACK された場合 (通常は発生しないが念のため)
      front.data.erase(front.data.begin(),
                       front.data.begin() + static_cast<ptrdiff_t>(remaining));
      remaining = 0;
    }
  }

  return 0;
}

int H3Session::stream_close_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // ストリーム終了イベント
  H3Event event;
  event.type = H3EventType::StreamClosed;
  event.stream_id = stream_id;
  event.error_code = app_error_code;

  // セッション ID を取得
  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
    session->stream_info_.erase(it);
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::recv_data_cb(nghttp3_conn* conn,
                            int64_t stream_id,
                            const uint8_t* data,
                            size_t datalen,
                            void* conn_user_data,
                            void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  H3Event event;
  event.type = H3EventType::StreamData;
  event.stream_id = stream_id;
  event.data.assign(data, data + datalen);

  // セッション ID を取得
  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::deferred_consume_cb(nghttp3_conn* conn,
                                   int64_t stream_id,
                                   size_t consumed,
                                   void* conn_user_data,
                                   void* stream_user_data) {
  (void)conn;
  (void)stream_id;
  (void)consumed;
  (void)conn_user_data;
  (void)stream_user_data;
  return 0;
}

int H3Session::begin_headers_cb(nghttp3_conn* conn,
                                int64_t stream_id,
                                void* conn_user_data,
                                void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);
  session->pending_headers_[stream_id].clear();
  return 0;
}

int H3Session::recv_header_cb(nghttp3_conn* conn,
                              int64_t stream_id,
                              int32_t token,
                              nghttp3_rcbuf* name,
                              nghttp3_rcbuf* value,
                              uint8_t flags,
                              void* conn_user_data,
                              void* stream_user_data) {
  (void)conn;
  (void)flags;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  nghttp3_vec name_vec = nghttp3_rcbuf_get_buf(name);
  nghttp3_vec value_vec = nghttp3_rcbuf_get_buf(value);

  std::string header_name(reinterpret_cast<const char*>(name_vec.base),
                          name_vec.len);
  std::string header_value(reinterpret_cast<const char*>(value_vec.base),
                           value_vec.len);

  session->pending_headers_[stream_id].emplace_back(std::move(header_name),
                                                    std::move(header_value));
  return 0;
}

int H3Session::end_headers_cb(nghttp3_conn* conn,
                              int64_t stream_id,
                              int fin,
                              void* conn_user_data,
                              void* stream_user_data) {
  (void)conn;
  (void)fin;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  auto it = session->pending_headers_.find(stream_id);
  if (it == session->pending_headers_.end()) {
    return 0;
  }

  const auto& headers = it->second;

  // WebTransport CONNECT リクエストかチェック
  bool is_connect = false;
  bool is_webtransport = false;
  std::string status;

  for (const auto& header : headers) {
    if (header.first == ":method" && header.second == "CONNECT") {
      is_connect = true;
    }
    if (header.first == ":protocol" && header.second == "webtransport") {
      is_webtransport = true;
    }
    if (header.first == ":status") {
      status = header.second;
    }
  }

  if (is_connect && is_webtransport && session->is_server_) {
    // サーバー: WebTransport セッションリクエストを受信
    session->session_ids_.insert(stream_id);

    H3Event event;
    event.type = H3EventType::SessionReady;
    event.session_id = stream_id;
    session->push_event(std::move(event));
  } else if (!session->is_server_ && status == "200") {
    // クライアント: WebTransport セッション確立
    if (session->session_ids_.count(stream_id) > 0) {
      H3Event event;
      event.type = H3EventType::SessionReady;
      event.session_id = stream_id;
      session->push_event(std::move(event));
    }
  }

  session->pending_headers_.erase(it);
  return 0;
}

int H3Session::stop_sending_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_id;
  (void)app_error_code;
  (void)conn_user_data;
  (void)stream_user_data;
  return 0;
}

int H3Session::reset_stream_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  H3Event event;
  event.type = H3EventType::StreamClosed;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  session->push_event(std::move(event));

  return 0;
}

int H3Session::shutdown_cb(nghttp3_conn* conn,
                           int64_t id,
                           void* conn_user_data) {
  (void)conn;
  (void)id;

  auto* session = static_cast<H3Session*>(conn_user_data);
  session->closed_ = true;
  return 0;
}

int H3Session::recv_settings2_cb(nghttp3_conn* conn,
                                 const nghttp3_proto_settings* settings,
                                 void* conn_user_data) {
  (void)conn;
  (void)settings;
  (void)conn_user_data;
  return 0;
}

int H3Session::recv_wt_data_cb(nghttp3_conn* conn,
                               int64_t session_id,
                               int64_t stream_id,
                               const uint8_t* data,
                               size_t datalen,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // 受信したストリームを stream_info_ に登録 (まだ登録されていない場合)
  // 書き込み登録はまだなので is_write_registered = false
  if (session->stream_info_.find(stream_id) == session->stream_info_.end()) {
    StreamInfo info;
    info.stream_id = stream_id;
    info.session_id = session_id;
    info.is_unidirectional = (stream_id & 0x2) != 0;
    info.is_incoming = true;
    info.is_write_registered = false;
    session->stream_info_[stream_id] = info;
  }

  H3Event event;
  event.type = H3EventType::StreamData;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + datalen);
  session->push_event(std::move(event));

  return 0;
}

// ========== Python バインディング ==========

void bind_webtransport_h3(nb::module_& m) {
  auto h3_mod = m.def_submodule("h3", "WebTransport over HTTP/3");

  // H3SessionConfig
  nb::class_<H3SessionConfig>(h3_mod, "Config", "WebTransport over HTTP/3 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("max_field_section_size",
              &H3SessionConfig::max_field_section_size)
      .def_rw("qpack_max_dtable_capacity",
              &H3SessionConfig::qpack_max_dtable_capacity)
      .def_rw("qpack_blocked_streams", &H3SessionConfig::qpack_blocked_streams)
      .def_rw("is_server", &H3SessionConfig::is_server);

  // H3EventType
  nb::enum_<H3EventType>(h3_mod, "EventType", "WebTransport イベント種別")
      .value("SESSION_READY", H3EventType::SessionReady)
      .value("SESSION_CLOSED", H3EventType::SessionClosed)
      .value("STREAM_OPENED", H3EventType::StreamOpened)
      .value("STREAM_DATA", H3EventType::StreamData)
      .value("STREAM_CLOSED", H3EventType::StreamClosed)
      .value("DATAGRAM", H3EventType::Datagram)
      .value("ERROR", H3EventType::Error);

  // H3Event
  nb::class_<H3Event>(h3_mod, "Event", "WebTransport イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &H3Event::type)
      .def_ro("session_id", &H3Event::session_id)
      .def_ro("stream_id", &H3Event::stream_id)
      .def_prop_ro(
          "data",
          [](const H3Event& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "イベントデータ")
      .def_ro("error_code", &H3Event::error_code)
      .def_ro("error_message", &H3Event::error_message)
      .def_ro("is_unidirectional", &H3Event::is_unidirectional);

  // StreamInfo
  nb::class_<StreamInfo>(h3_mod, "StreamInfo", "WebTransport ストリーム情報")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("stream_id", &StreamInfo::stream_id)
      .def_ro("session_id", &StreamInfo::session_id)
      .def_ro("is_unidirectional", &StreamInfo::is_unidirectional)
      .def_ro("is_incoming", &StreamInfo::is_incoming)
      .def_ro("is_write_registered", &StreamInfo::is_write_registered);

  // H3Session
  nb::class_<H3Session>(h3_mod, "Session",
                        "WebTransport over HTTP/3 セッション")
      .def_static(
          "create_client",
          [](const H3SessionConfig& config) {
            auto session = H3Session::create_client(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H3 client session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Session"),
          "クライアントセッションを作成")
      .def_static(
          "create_server",
          [](const H3SessionConfig& config) {
            auto session = H3Session::create_server(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H3 server session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Session"),
          "サーバーセッションを作成")
      .def(
          "receive_stream_data",
          [](H3Session& s, int64_t stream_id, nb::bytes data, bool fin) {
            return s.receive_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def receive_stream_data(self, stream_id: int, data: "
                  "bytes, fin: bool = False) -> int"),
          "QUIC ストリームからデータを受信")
      .def(
          "receive_datagram",
          [](H3Session& s, nb::bytes data) {
            s.receive_datagram(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"),
          nb::sig("def receive_datagram(self, data: bytes) -> None"),
          "QUIC データグラムを受信")
      .def(
          "get_streams_to_send",
          [](H3Session& s) {
            auto streams = s.get_streams_to_send();
            std::vector<std::tuple<int64_t, nb::bytes, bool>> result;
            result.reserve(streams.size());
            for (auto& [stream_id, data, fin] : streams) {
              result.emplace_back(
                  stream_id,
                  nb::bytes(reinterpret_cast<const char*>(data.data()),
                            data.size()),
                  fin);
            }
            return result;
          },
          nb::sig("def get_streams_to_send(self) -> list[tuple[int, "
                  "bytes, bool]]"),
          "送信すべきストリームデータを取得")
      .def(
          "get_datagrams_to_send",
          [](H3Session& s) {
            auto datagrams = s.get_datagrams_to_send();
            std::vector<nb::bytes> result;
            result.reserve(datagrams.size());
            for (auto& data : datagrams) {
              result.emplace_back(nb::bytes(
                  reinterpret_cast<const char*>(data.data()), data.size()));
            }
            return result;
          },
          nb::sig("def get_datagrams_to_send(self) -> list[bytes]"),
          "送信すべきデータグラムを取得")
      .def("bind_control_stream", &H3Session::bind_control_stream,
           nb::arg("stream_id"),
           nb::sig("def bind_control_stream(self, stream_id: int) -> None"),
           "コントロールストリーム ID を設定")
      .def("bind_qpack_encoder_stream", &H3Session::bind_qpack_encoder_stream,
           nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_encoder_stream(self, stream_id: int) -> None"),
           "QPACK エンコーダーストリーム ID を設定")
      .def("bind_qpack_decoder_stream", &H3Session::bind_qpack_decoder_stream,
           nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_decoder_stream(self, stream_id: int) -> None"),
           "QPACK デコーダーストリーム ID を設定")
      .def("connect", &H3Session::connect, nb::arg("stream_id"), nb::arg("url"),
           nb::sig("def connect(self, stream_id: int, url: str) -> bool"),
           "WebTransport セッションを開始 (クライアント用)")
      .def("accept_session", &H3Session::accept_session, nb::arg("stream_id"),
           nb::sig("def accept_session(self, stream_id: int) -> bool"),
           "WebTransport セッションを受理 (サーバー用)")
      .def("reject_session", &H3Session::reject_session, nb::arg("stream_id"),
           nb::arg("status_code"),
           nb::sig("def reject_session(self, stream_id: int, status_code: int) "
                   "-> None"),
           "WebTransport セッションを拒否 (サーバー用)")
      .def("open_stream", &H3Session::open_stream, nb::arg("session_id"),
           nb::arg("stream_id"), nb::arg("is_unidirectional"),
           nb::sig("def open_stream(self, session_id: int, stream_id: int, "
                   "is_unidirectional: bool) -> bool"),
           "WebTransport ストリームを開く")
      .def(
          "send_stream_data",
          [](H3Session& s, int64_t stream_id, nb::bytes data, bool fin) {
            s.send_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def send_stream_data(self, stream_id: int, data: "
                  "bytes, fin: bool = False) -> None"),
          "WebTransport ストリームにデータを送信")
      .def(
          "send_datagram",
          [](H3Session& s, int64_t session_id, nb::bytes data) {
            s.send_datagram(
                session_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("session_id"), nb::arg("data"),
          nb::sig("def send_datagram(self, session_id: int, data: bytes) "
                  "-> None"),
          "WebTransport データグラムを送信")
      .def("close_stream", &H3Session::close_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def close_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "WebTransport ストリームを閉じる")
      .def("close_session", &H3Session::close_session, nb::arg("session_id"),
           nb::arg("error_code") = 0, nb::arg("error_message") = "",
           nb::sig("def close_session(self, session_id: int, error_code: int = "
                   "0, error_message: str = '') -> None"),
           "WebTransport セッションを閉じる")
      .def("next_event", &H3Session::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("get_required_streams", &H3Session::get_required_streams,
           nb::sig("def get_required_streams(self) -> list[tuple[str, bool]]"),
           "必要な QUIC ストリーム ID のリストを取得")
      .def("is_closed", &H3Session::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def("get_session_ids", &H3Session::get_session_ids,
           nb::sig("def get_session_ids(self) -> list[int]"),
           "確立されたセッション ID のリストを取得")
      .def("get_session_streams", &H3Session::get_session_streams,
           nb::arg("session_id"),
           nb::sig("def get_session_streams(self, session_id: int) -> "
                   "list[StreamInfo]"),
           "セッションに属するストリームを取得")
      .def("set_max_client_streams_bidi",
           &H3Session::set_max_client_streams_bidi, nb::arg("max_streams"),
           nb::sig("def set_max_client_streams_bidi(self, max_streams: int) -> "
                   "None"),
           "クライアントからの双方向ストリームの最大数を設定");
}

}  // namespace h3
}  // namespace webtransport
