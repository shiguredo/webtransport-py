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
      pending_trailers_(std::move(other.pending_trailers_)),
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
    pending_trailers_ = std::move(other.pending_trailers_);
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

  // PRIORITY_UPDATE (RFC 9218) を拡張フレームとして受信するための
  // オプションを設定する。PRIORITY_UPDATE はクライアントのみが送信する
  // フレーム (nghttp2 もサーバーセッションの送信を拒否する) のため、
  // サーバーセッションのみ登録する。クライアントセッションに登録すると
  // 不正なサーバーから受信した際に nghttp2 が PROTOCOL_ERROR でセッション
  // を終了するため、登録せず未知フレームとして無視させる
  nghttp2_option* option = nullptr;
  rv = nghttp2_option_new(&option);
  if (rv != 0) {
    nghttp2_session_callbacks_del(callbacks);
    return false;
  }
  if (is_server_) {
    nghttp2_option_set_builtin_recv_extension_type(option,
                                                   NGHTTP2_PRIORITY_UPDATE);
  }

  // セッションを作成
  if (is_server_) {
    rv = nghttp2_session_server_new2(&session_, callbacks, this, option);
  } else {
    rv = nghttp2_session_client_new2(&session_, callbacks, this, option);
  }

  nghttp2_option_del(option);
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
      {NGHTTP2_SETTINGS_NO_RFC7540_PRIORITIES, config_.no_rfc7540_priorities},
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

  // データプロバイダを常に渡す。未設定だと nghttp2 が HEADERS に
  // END_STREAM を付け、後続の send_data が DATA を送出できなくなる。
  // リクエストの終端は send_data(..., eof=True) で行う
  nghttp2_data_provider data_prd;
  data_prd.source.ptr = this;
  data_prd.read_callback = data_source_read_callback;

  int32_t stream_id = nghttp2_submit_request(session_, nullptr, nva.data(),
                                             nva.size(), &data_prd, nullptr);

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

  // トレーラを予約済みのストリームでは、トレーラ HEADERS が END_STREAM
  // を担うため eof=True の END_STREAM 付与を無効化する (eof=True の DATA
  // 送出でローカル側が half-closed になるとトレーラを送れなくなるため。
  // トレーラの予約が無ければ従来どおり eof=True で終端する)
  if (eof && pending_trailers_.count(stream_id) != 0) {
    eof = false;
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

bool Http2Connection::terminate_session(uint32_t error_code,
                                        int32_t last_stream_id) {
  if (!session_ || closed_) {
    return false;
  }

  // last_stream_id はピアが開始したストリーム ID (0 は全ストリーム終了)。
  // クライアントセッションでは偶数 / サーバーセッションでは奇数が有効。
  // パリティ違反を nghttp2 に渡すと INVALID_ARGUMENT を返すが、その前に
  // セッションの受信処理を無視状態 (IB_IGN_ALL) にしてしまうため、
  // 事前にガードする。負の値も nghttp2 を素通りして不正な GOAWAY を
  // 送出してしまうため同様に拒否する
  if (last_stream_id != 0) {
    bool is_even = (last_stream_id & 0x1) == 0;
    bool valid = is_server_ ? !is_even : is_even;
    if (last_stream_id < 0 || !valid) {
      return false;
    }
  }

  // GOAWAY を送信してセッションを即時終了する。呼び出し直後から受信
  // フレームを無視し、GOAWAY 送出後に want_read / want_write が 0 になる。
  // closed_ にはしない (send() を止めないため)。2 回目以降の呼び出しは
  // GOAWAY_TERM_ON_SEND が立っているため何もせず成功を返す
  return nghttp2_session_terminate_session2(session_, last_stream_id,
                                            error_code) == 0;
}

bool Http2Connection::set_local_window_size(int32_t stream_id,
                                            int32_t window_size) {
  if (!session_ || closed_) {
    return false;
  }

  // 負のウィンドウサイズはガードする (nghttp2 も INVALID_ARGUMENT を返す)
  if (window_size < 0) {
    return false;
  }

  // 増加時は WINDOW_UPDATE がキュー投入され、減少時はローカルでの
  // 受信絞り込みのみになる。存在しないストリームと負の stream_id は
  // 成功扱い (nghttp2 v1.70.0 の実装)
  return nghttp2_session_set_local_window_size(session_, NGHTTP2_FLAG_NONE,
                                               stream_id, window_size) == 0;
}

// ========== メッセージング拡張 API ==========

bool Http2Connection::submit_trailer(
    int32_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!session_ || closed_ || !is_server_) {
    return false;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない
  if (stream_id <= 0) {
    return false;
  }

  // ストリームが存在しない (まだ受信していない・既に閉じた) 場合は送信
  // できない。ローカル側が half-closed (END_STREAM 送出済み) のストリーム
  // にもトレーラを送れない (RFC 9113 8.1 節)
  int local_close = nghttp2_session_get_stream_local_close(session_, stream_id);
  if (local_close < 0) {
    return false;
  }
  if (local_close != 0) {
    return false;
  }

  // レスポンス (データプロバイダ) が設定されていないストリームには
  // トレーラを送信できない (submit_response が stream_buffers_ を作成し、
  // data_source_read_callback が呼ばれるための前提となる)
  auto it = stream_buffers_.find(stream_id);
  if (it == stream_buffers_.end()) {
    return false;
  }

  // eof=True の送信データが積まれている場合は END_STREAM 付き DATA の
  // 送出後にトレーラを送れないため拒否する
  for (const auto& sd : it->second) {
    if (sd.eof) {
      return false;
    }
  }

  // トレーラセクションは 1 つのみ (RFC 9113 8.1 節) のため、既に予約済み
  // のストリームへの再予約は受け付けない
  if (pending_trailers_.count(stream_id) != 0) {
    return false;
  }

  // トレーラは保留し、data_source_read_callback がデータの最終チャンクを
  // 返す時点で nghttp2_submit_trailer を呼ぶ (直接キューに積むと
  // ヘッダー系が DATA より先に送信されるため)
  pending_trailers_[stream_id] = headers;

  // 送信データが既に flush され deferred 状態になっている場合は再開する
  // (まだ送信待ちデータがある場合は resume が INVALID_ARGUMENT を返すが
  // 無視してよい。データ送出後にトレーラが送信される)
  nghttp2_session_resume_data(session_, stream_id);

  return true;
}

bool Http2Connection::submit_priority_update(int32_t stream_id,
                                             uint32_t urgency,
                                             bool incremental) {
  if (!session_ || closed_ || is_server_) {
    return false;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない
  // (nghttp2 は noop 判定を stream_id 検証より先に行うため、ピア設定
  // 次第で成功と失敗が切り替わる。C++ 側で一貫して拒否する)
  if (stream_id <= 0) {
    return false;
  }

  // RFC 9218 で定義された urgency の範囲 (0-7) を超える場合はガードする
  // (nghttp2 は検証せず素通しするため、H3 側の client_stream_priority
  // と同じセマンティクスで拒否する)
  if (urgency > NGHTTP2_EXTPRI_URGENCY_LOW) {
    return false;
  }

  // Priority field value (RFC 9218 6.3 節) をシリアライズする
  // (u={urgency}、incremental のとき , i)
  std::string field_value = "u=" + std::to_string(urgency);
  if (incremental) {
    field_value += ", i";
  }

  // 動作にはピアの SETTINGS_NO_RFC7540_PRIORITIES=1 が必要 (ピアが SETTINGS
  // で NO_RFC7540_PRIORITIES=0 を送信した場合のみ nghttp2 が noop で成功を
  // 返す。未受信時は内部値が UINT32_MAX のためフレームが送出される)
  return nghttp2_submit_priority_update(
             session_, NGHTTP2_FLAG_NONE, stream_id,
             reinterpret_cast<const uint8_t*>(field_value.c_str()),
             field_value.size()) == 0;
}

bool Http2Connection::change_extpri_stream_priority(int32_t stream_id,
                                                    uint32_t urgency,
                                                    bool incremental) {
  if (!session_ || closed_ || !is_server_) {
    return false;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない
  // (nghttp2 は noop 判定を stream_id 検証より先に行うため、ピア設定
  // 次第で成功と失敗が切り替わる。C++ 側で一貫して拒否する)
  if (stream_id <= 0) {
    return false;
  }

  // RFC 9218 で定義された urgency の範囲 (0-7) を超える場合はガードする
  // (nghttp2 は EXTPRI_URGENCY_LOW にクランプするだけのため、H3 側の
  // server_stream_priority と同じセマンティクスで拒否する)
  if (urgency > NGHTTP2_EXTPRI_URGENCY_LOW) {
    return false;
  }

  nghttp2_extpri extpri;
  extpri.urgency = urgency;
  extpri.inc = incremental ? 1 : 0;

  // ignore_client_signal は常に 1 (サーバーが設定した優先度を優先し、
  // クライアントからの優先度更新を無視する)。動作には自己の
  // SETTINGS_NO_RFC7540_PRIORITIES=1 の送信が必要 (未送信時は nghttp2 が
  // noop で成功を返す)
  return nghttp2_session_change_extpri_stream_priority(session_, stream_id,
                                                       &extpri, 1) == 0;
}

int32_t Http2Connection::submit_push_promise(
    int32_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!session_ || closed_ || !is_server_) {
    return -1;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない
  if (stream_id <= 0) {
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

  // 成功で promised stream ID を返す。失敗時は nghttp2 の負のエラーコード
  // を -1 に正規化して返す (既存の submit_request と同じ契約)
  int32_t rv = nghttp2_submit_push_promise(
      session_, NGHTTP2_FLAG_NONE, stream_id, nva.data(), nva.size(), nullptr);
  return rv < 0 ? -1 : rv;
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

    case NGHTTP2_PUSH_PROMISE:
      if (frame->hd.flags & NGHTTP2_FLAG_END_HEADERS) {
        // プッシュリクエストのヘッダー受信完了
        auto it = self->pending_headers_.find(frame->hd.stream_id);
        if (it != self->pending_headers_.end()) {
          Http2Event event;
          event.type = Http2EventType::PushPromise;
          event.stream_id = frame->hd.stream_id;
          event.promised_stream_id = frame->push_promise.promised_stream_id;
          event.headers = std::move(it->second);
          self->push_event(std::move(event));
          self->pending_headers_.erase(it);
        }
      }
      break;

    case NGHTTP2_PRIORITY_UPDATE: {
      // 拡張フレームのペイロードから優先度が更新されたストリーム ID と
      // priority field value を取り出す (フレーム自身の stream_id は 0)。
      // builtin recv extension として登録済みのため通常は必ずデコード
      // されるが、防御的にガード
      auto* ext = static_cast<nghttp2_ext_priority_update*>(frame->ext.payload);
      if (ext == nullptr) {
        break;
      }
      Http2Event event;
      event.type = Http2EventType::PriorityUpdate;
      event.stream_id = ext->stream_id;
      // field_value は null 終端ではない (nghttp2.h の doc)。ペイロードが
      // stream_id のみの場合は field_value が NULL になるためガードする
      event.priority_field_value =
          ext->field_value_len > 0
              ? std::string(reinterpret_cast<const char*>(ext->field_value),
                            ext->field_value_len)
              : std::string();
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
  // ストリームが閉じた時点でトレーラも送信できなくなるため、保留中の
  // トレーラを破棄する (RST_STREAM 送信・受信などで残留しないようにする)
  self->pending_trailers_.erase(stream_id);
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

  if (frame->hd.type == NGHTTP2_HEADERS ||
      frame->hd.type == NGHTTP2_PUSH_PROMISE) {
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

  if (frame->hd.type == NGHTTP2_HEADERS ||
      frame->hd.type == NGHTTP2_PUSH_PROMISE) {
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
    // 送信データが尽きた。保留中のトレーラがあれば、ここで EOF を立てて
    // トレーラ HEADERS を続けて送る (nghttp2.h の nghttp2_submit_trailer
    // の説明に従う)。トレーラが無ければ従来どおり保留状態にする
    auto trailer_it = self->pending_trailers_.find(stream_id);
    if (trailer_it == self->pending_trailers_.end()) {
      return NGHTTP2_ERR_DEFERRED;
    }

    // トレーラを nghttp2_nv に変換
    std::vector<nghttp2_nv> nva;
    nva.reserve(trailer_it->second.size());
    for (const auto& [name, value] : trailer_it->second) {
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

    *data_flags |= NGHTTP2_DATA_FLAG_EOF;
    if (nghttp2_submit_trailer(self->session_, stream_id, nva.data(),
                               nva.size()) == 0) {
      // トレーラ HEADERS が END_STREAM を担うため END_STREAM を立てない
      *data_flags |= NGHTTP2_DATA_FLAG_NO_END_STREAM;
    }
    self->pending_trailers_.erase(trailer_it);
    return 0;
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
              "HTTP/2 プリフェイスを送信するか")
      .def_rw("no_rfc7540_priorities", &Http2Config::no_rfc7540_priorities,
              "SETTINGS_NO_RFC7540_PRIORITIES を送信するか");

  // Http2EventType
  nb::enum_<Http2EventType>(http2_m, "EventType", "HTTP/2 イベント種別")
      .value("HEADERS", Http2EventType::Headers)
      .value("DATA", Http2EventType::Data)
      .value("STREAM_END", Http2EventType::StreamEnd)
      .value("STREAM_RESET", Http2EventType::StreamReset)
      .value("GO_AWAY", Http2EventType::GoAway)
      .value("WINDOW_UPDATE", Http2EventType::WindowUpdate)
      .value("SETTINGS", Http2EventType::Settings)
      .value("PING", Http2EventType::Ping)
      .value("PUSH_PROMISE", Http2EventType::PushPromise)
      .value("PRIORITY_UPDATE", Http2EventType::PriorityUpdate);

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
              "GOAWAY の last_stream_id")
      .def_ro("promised_stream_id", &Http2Event::promised_stream_id,
              "PUSH_PROMISE の promised stream ID")
      .def_ro("priority_field_value", &Http2Event::priority_field_value,
              "PRIORITY_UPDATE の priority field value");

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
           "リクエストを送信 (終端は send_data の eof=True で行う)")
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
      .def("terminate_session", &Http2Connection::terminate_session,
           nb::arg("error_code") = 0, nb::arg("last_stream_id") = 0,
           nb::sig("def terminate_session(self, error_code: int = 0, "
                   "last_stream_id: int = 0) -> bool"),
           "GOAWAY を送信してセッションを即時終了")
      .def("set_local_window_size", &Http2Connection::set_local_window_size,
           nb::arg("stream_id"), nb::arg("window_size"),
           nb::sig("def set_local_window_size(self, stream_id: int, "
                   "window_size: int) -> bool"),
           "ローカルウィンドウサイズを動的に変更")
      .def("submit_trailer", &Http2Connection::submit_trailer,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_trailer(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "トレーラを送信")
      .def("submit_priority_update", &Http2Connection::submit_priority_update,
           nb::arg("stream_id"), nb::arg("urgency"), nb::arg("incremental"),
           nb::sig("def submit_priority_update(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "PRIORITY_UPDATE フレームを送信")
      .def("change_extpri_stream_priority",
           &Http2Connection::change_extpri_stream_priority,
           nb::arg("stream_id"), nb::arg("urgency"), nb::arg("incremental"),
           nb::sig("def change_extpri_stream_priority(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "ストリームの優先度を変更")
      .def("submit_push_promise", &Http2Connection::submit_push_promise,
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_push_promise(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> int"),
           "Server Push を宣言")
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

  // ALPN プロトコル選択 (サーバー用ユーティリティ)
  http2_m.def(
      "select_alpn",
      [](const std::vector<std::string>& client_protocols)
          -> std::optional<std::string> {
        // length-prefixed のワイヤ形式 (ALPN プロトコルリスト) に変換する。
        // RFC 7301 のプロトコル名は 1-255 バイトのため、長さが 256 バイト
        // 以上のエントリはワイヤ形式に変換できないので無視する
        std::vector<uint8_t> wire;
        for (const auto& proto : client_protocols) {
          if (proto.size() > 255) {
            continue;
          }
          wire.push_back(static_cast<uint8_t>(proto.size()));
          wire.insert(wire.end(), proto.begin(), proto.end());
        }

        // 戻り値は 1 = h2 選択 / 0 = http/1.1 選択 / -1 = 一致なし
        const uint8_t* out = nullptr;
        uint8_t outlen = 0;
        int rv = nghttp2_select_alpn(&out, &outlen, wire.data(), wire.size());
        if (rv < 0) {
          return std::nullopt;
        }
        return std::string(reinterpret_cast<const char*>(out), outlen);
      },
      nb::arg("client_protocols"),
      nb::sig("def select_alpn(client_protocols: list[str]) -> str | None"),
      "ALPN プロトコルを選択 (h2 / http/1.1 の優先順)");
}

}  // namespace http2
}  // namespace webtransport
