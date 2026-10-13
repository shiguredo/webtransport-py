/**
 * HTTP/2 バインディング (nghttp2-v2 ラッパー)
 *
 * Sans-IO スタイルの HTTP/2 実装
 */

#include "http2.h"

#include "header_convert.h"
#include "python_input.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <random>
#include <stdexcept>

namespace webtransport {
namespace http2 {

namespace {

// nghttp2_v2 のタイムスタンプはナノ秒解像度 (nghttp2_tstamp)
nghttp2_tstamp now_ts() {
  return static_cast<nghttp2_tstamp>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

}  // namespace

// データプロバイダ。nghttp2-v2 は v1 の data_source を廃止し、
// nghttp2_vec にアプリのバッファを直接指す方式になった
const nghttp2_data_reader* Http2Connection::data_reader() {
  static const nghttp2_data_reader dr = {
      .read_data = &Http2Connection::on_read_data_callback,
  };
  return &dr;
}

// ========== Http2Connection 実装 ==========

Http2Connection::Http2Connection(bool is_server, const Http2Config& config)
    : is_server_(is_server), config_(config) {}

Http2Connection::~Http2Connection() {
  if (conn_) {
    nghttp2_conn_del(conn_);
  }
}

Http2Connection::Http2Connection(Http2Connection&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      conn_(other.conn_),
      events_(std::move(other.events_)),
      pending_out_(std::move(other.pending_out_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      stream_data_sent_(std::move(other.stream_data_sent_)),
      trailers_submitted_(std::move(other.trailers_submitted_)),
      local_resets_(std::move(other.local_resets_)),
      streams_(std::move(other.streams_)),
      pending_headers_(std::move(other.pending_headers_)),
      pending_trailers_(std::move(other.pending_trailers_)),
      conn_local_window_size_(other.conn_local_window_size_),
      conn_recv_window_size_(other.conn_recv_window_size_),
      conn_recv_reduction_(other.conn_recv_reduction_),
      stream_local_window_size_(std::move(other.stream_local_window_size_)),
      stream_recv_window_size_(std::move(other.stream_recv_window_size_)),
      stream_recv_reduction_(std::move(other.stream_recv_reduction_)),
      closed_(other.closed_),
      closing_(other.closing_),
      goaway_sent_(other.goaway_sent_),
      goaway_received_(other.goaway_received_) {
  other.conn_ = nullptr;
}

Http2Connection& Http2Connection::operator=(Http2Connection&& other) noexcept {
  if (this != &other) {
    if (conn_) {
      nghttp2_conn_del(conn_);
    }

    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    conn_ = other.conn_;
    events_ = std::move(other.events_);
    pending_out_ = std::move(other.pending_out_);
    stream_buffers_ = std::move(other.stream_buffers_);
    stream_data_sent_ = std::move(other.stream_data_sent_);
    trailers_submitted_ = std::move(other.trailers_submitted_);
    local_resets_ = std::move(other.local_resets_);
    streams_ = std::move(other.streams_);
    pending_headers_ = std::move(other.pending_headers_);
    pending_trailers_ = std::move(other.pending_trailers_);
    conn_local_window_size_ = other.conn_local_window_size_;
    conn_recv_window_size_ = other.conn_recv_window_size_;
    conn_recv_reduction_ = other.conn_recv_reduction_;
    stream_local_window_size_ = std::move(other.stream_local_window_size_);
    stream_recv_window_size_ = std::move(other.stream_recv_window_size_);
    stream_recv_reduction_ = std::move(other.stream_recv_reduction_);
    closed_ = other.closed_;
    closing_ = other.closing_;
    goaway_sent_ = other.goaway_sent_;
    goaway_received_ = other.goaway_received_;

    other.conn_ = nullptr;
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
  // nghttp2_conn_server_new / nghttp2_conn_client_new は
  // settings.initial_max_stream_data を INT32_MAX 以下で assert する。
  // assert 失敗はプロセスの abort になるため境界で拒否する
  if (config_.initial_window_size > static_cast<uint32_t>(INT32_MAX)) {
    return false;
  }

  // nghttp2-v2 のコールバックは nghttp2_callbacks 構造体で渡す。rand は
  // 必須 (接続生成時にレートリミッタの種に使われる)
  nghttp2_callbacks callbacks = {};
  callbacks.rand = &Http2Connection::on_rand_callback;
  callbacks.stream_open = &Http2Connection::on_stream_open_callback;
  callbacks.stream_close = &Http2Connection::on_stream_close_callback;
  callbacks.recv_settings_entry =
      &Http2Connection::on_recv_settings_entry_callback;
  callbacks.recv_settings = &Http2Connection::on_recv_settings_callback;
  callbacks.recv_settings_ack = &Http2Connection::on_recv_settings_ack_callback;
  callbacks.begin_headers = &Http2Connection::on_begin_headers_callback;
  callbacks.recv_header = &Http2Connection::on_recv_header_callback;
  callbacks.end_headers = &Http2Connection::on_end_headers_callback;
  callbacks.begin_trailers = &Http2Connection::on_begin_trailers_callback;
  callbacks.recv_trailer = &Http2Connection::on_recv_trailer_callback;
  callbacks.end_trailers = &Http2Connection::on_end_trailers_callback;
  callbacks.recv_data = &Http2Connection::on_recv_data_callback;
  callbacks.remote_end_stream = &Http2Connection::on_remote_end_stream_callback;
  callbacks.local_end_stream = &Http2Connection::on_local_end_stream_callback;
  callbacks.recv_ping_ack = &Http2Connection::on_recv_ping_ack_callback;
  callbacks.shutdown = &Http2Connection::on_shutdown_callback;
  callbacks.write_stream_data_offset =
      &Http2Connection::on_write_stream_data_offset_callback;

  nghttp2_settings settings;
  nghttp2_settings_default(&settings);
  settings.initial_ts = now_ts();
  // SETTINGS_MAX_CONCURRENT_STREAMS として広告する値
  settings.max_concurrent_streams_remote = config_.max_concurrent_streams;
  // SETTINGS_INITIAL_WINDOW_SIZE として広告する値。nghttp2-v2 は受信ウィンドウ
  // の管理をアプリに委ねるため、この値がストリームの受信ウィンドウの初期値に
  // なる (本クラスは v1 相当の自動 WINDOW_UPDATE を実装する)
  settings.initial_max_stream_data = config_.initial_window_size;
  // SETTINGS_MAX_FRAME_SIZE / SETTINGS_MAX_HEADER_LIST_SIZE は nghttp2-v2 が
  // 扱わないため送信しない (フレームサイズは 16384 固定、ヘッダーリスト
  // サイズはローカルでは無制限)

  int rv;
  if (is_server_) {
    rv = nghttp2_conn_server_new(&conn_, &callbacks, &settings, nullptr, this);
  } else {
    rv = nghttp2_conn_client_new(&conn_, &callbacks, &settings, nullptr, this);
  }

  if (rv != 0) {
    return false;
  }

  return true;
}

bool Http2Connection::ensure_pending_out() {
  if (!conn_ || closed_ || closing_) {
    return false;
  }

  if (!pending_out_.empty()) {
    return true;
  }

  // nghttp2-v2 はアプリ提供バッファへ書き込む。TLS レコードに収まる
  // 16384 バイトを 1 チャンクとして取り出す
  std::array<uint8_t, 16384> buf;
  nghttp2_ssize n = nghttp2_conn_write(conn_, buf.data(), buf.size(), now_ts());
  if (n < 0) {
    if (n == NGHTTP2_ERR_CLOSING) {
      // GOAWAY の送出が完了し、接続を閉じてよい状態
      closing_ = true;
      return false;
    }
    closed_ = true;
    return false;
  }

  if (n == 0) {
    return false;
  }

  pending_out_.assign(buf.data(), buf.data() + n);
  return true;
}

size_t Http2Connection::receive(const std::vector<uint8_t>& data) {
  if (!conn_ || closed_) {
    return 0;
  }

  // nghttp2-v2 の nghttp2_conn_read は入力全体を処理する (v1 の
  // nghttp2_session_mem_recv2 と異なり処理バイト数を返さない)
  int rv = nghttp2_conn_read(conn_, data.data(), data.size(), now_ts());
  if (rv != 0) {
    closed_ = true;
    return 0;
  }

  return data.size();
}

std::optional<std::vector<uint8_t>> Http2Connection::send() {
  if (!ensure_pending_out()) {
    return std::nullopt;
  }

  std::vector<uint8_t> result;
  result.swap(pending_out_);
  return result;
}

int64_t Http2Connection::submit_request(
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_ || is_server_ || goaway_received_) {
    return -1;
  }

  std::vector<nghttp2_nv> nva = bindings::to_nghttp2_nv(headers);

  // データプロバイダを常に渡す。未設定だと nghttp2 が HEADERS に
  // END_STREAM を付け、後続の send_data が DATA を送出できなくなる。
  // リクエストの終端は send_data(..., eof=True) で行う
  int64_t stream_id = nghttp2_conn_submit_request(conn_, nva.data(), nva.size(),
                                                  data_reader(), this);

  if (stream_id < 0) {
    return -1;
  }

  register_stream(stream_id);
  stream_buffers_[stream_id].clear();
  return stream_id;
}

void Http2Connection::submit_response(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  // nghttp2-v2 はストリーム ID を assert で検証する (失敗するとプロセスが
  // abort する) ため、不正な ID は境界で弾く
  if (!conn_ || closed_ || !is_server_ || stream_id <= 0 ||
      stream_id > INT32_MAX || (stream_id & 1) == 0) {
    return;
  }

  std::vector<nghttp2_nv> nva = bindings::to_nghttp2_nv(headers);

  stream_buffers_[stream_id].clear();

  // 1xx 中間応答は nghttp2-v2 では nghttp2_conn_submit_info で送る
  // (submit_response は最終応答用で 1 回のみ。v1 互換のため :status の
  // 値で振り分ける)
  bool informational = false;
  for (const auto& [name, value] : headers) {
    if (name == ":status" && !value.empty() && value[0] == '1') {
      informational = true;
      break;
    }
  }

  if (informational) {
    nghttp2_conn_submit_info(conn_, stream_id, nva.data(), nva.size());
    return;
  }

  nghttp2_conn_submit_response(conn_, stream_id, nva.data(), nva.size(),
                               data_reader());
}

void Http2Connection::send_data(int64_t stream_id,
                                const std::vector<uint8_t>& data,
                                bool eof) {
  if (!conn_ || closed_) {
    return;
  }

  // トレーラを送信済みのストリームでは、トレーラ HEADERS が END_STREAM
  // を担うため eof=True の END_STREAM 付与を無効化する
  if (eof && trailers_submitted_.count(stream_id) != 0) {
    eof = false;
  }

  stream_buffers_[stream_id].push_back({.data = data, .eof = eof});
  // nghttp2-v2 では read_data が NGHTTP2_ERR_WOULDBLOCK を返した後に
  // resume_stream で再開する
  nghttp2_conn_resume_stream(conn_, stream_id);
}

void Http2Connection::reset_stream(int64_t stream_id, uint32_t error_code) {
  if (!conn_ || closed_ || stream_id <= 0 || stream_id > INT32_MAX) {
    return;
  }

  local_resets_.insert(stream_id);
  nghttp2_conn_shutdown_stream(conn_, 0, stream_id, error_code);
  stream_buffers_.erase(stream_id);
}

void Http2Connection::goaway(uint32_t error_code) {
  if (!conn_ || closed_ || goaway_sent_) {
    return;
  }

  if (error_code != NGHTTP2_NO_ERROR) {
    // nghttp2-v2 の graceful shutdown はエラーコードに NO_ERROR を固定する
    // ため、0 以外は即時終了にフォールバックしてコードを伝える
    terminate_session(error_code, 0);
    return;
  }

  // last_stream_id にはライブラリが処理済みの最大ストリーム ID を使う
  nghttp2_conn_shutdown(conn_);
  goaway_sent_ = true;
}

void Http2Connection::test_force_close() {
  // テスト専用。nghttp2_conn_read / nghttp2_conn_write が負値を返した経路と
  // 同じ状態 (closed_ のみを立て、イベントは push しない)
  closed_ = true;
}

size_t Http2Connection::test_stream_buffer_count(int64_t stream_id) const {
  auto it = stream_buffers_.find(stream_id);
  return it == stream_buffers_.end() ? 0 : it->second.size();
}

size_t Http2Connection::test_stream_buffer_remaining(int64_t stream_id) const {
  auto it = stream_buffers_.find(stream_id);
  if (it == stream_buffers_.end() || it->second.empty()) {
    return 0;
  }
  const auto& front = it->second.front();
  return front.data.size() - front.offset;
}

size_t Http2Connection::test_stream_buffer_offset(int64_t stream_id) const {
  auto it = stream_buffers_.find(stream_id);
  if (it == stream_buffers_.end() || it->second.empty()) {
    return 0;
  }
  return it->second.front().offset;
}

void Http2Connection::ping(const std::vector<uint8_t>& opaque_data) {
  if (!conn_ || closed_) {
    return;
  }

  // RFC 9113 Section 6.7 の PING は 8 バイト固定。空 (未指定) はゼロ 8 バイト
  if (!opaque_data.empty() && opaque_data.size() != 8) {
    throw std::runtime_error("ping opaque_data must be exactly 8 bytes");
  }

  nghttp2_ping_data data;
  std::memset(data.data, 0, sizeof(data.data));
  if (!opaque_data.empty()) {
    std::memcpy(data.data, opaque_data.data(), sizeof(data.data));
  }

  // nghttp2-v2 は同時に 1 つの PING のみ in-flight を許す。既に in-flight の
  // 場合は NGHTTP2_ERR_INVALID_STATE が返るが、v1 互換のため黙って無視する
  (void)nghttp2_conn_submit_ping(conn_, &data);
}

bool Http2Connection::terminate_session(uint32_t error_code,
                                        int64_t last_stream_id) {
  if (!conn_ || closed_) {
    return false;
  }

  // last_stream_id はピアが開始したストリーム ID (0 は全ストリーム終了)。
  // クライアントセッションでは偶数 / サーバーセッションでは奇数が有効。
  // nghttp2-v2 の nghttp2_conn_terminate は last_stream_id を受け取らない
  // ため、従来と同じ入力検証だけを行い、値はライブラリに任せる
  if (last_stream_id != 0) {
    bool is_even = (last_stream_id & 0x1) == 0;
    bool valid = is_server_ ? !is_even : is_even;
    if (last_stream_id < 0 || !valid) {
      return false;
    }
  }

  // GOAWAY を送出し、以後の受信フレームを破棄する即時終了
  nghttp2_conn_terminate(conn_, error_code);
  goaway_sent_ = true;
  return true;
}

bool Http2Connection::set_local_window_size(int64_t stream_id,
                                            int32_t window_size) {
  if (!conn_ || closed_) {
    return false;
  }

  if (window_size < 0) {
    return false;
  }

  if (stream_id == 0) {
    int64_t delta = static_cast<int64_t>(window_size) - conn_local_window_size_;
    if (delta == 0) {
      return true;
    }
    if (delta < 0) {
      // 減少はピアへ通知せず、受信ウィンドウを絞る
      conn_local_window_size_ += delta;
      conn_recv_window_size_ += delta;
      conn_recv_reduction_ -= delta;
      return true;
    }

    // 増加は WINDOW_UPDATE で通知する。過去の減少分 (recv_reduction) を
    // 先に取り戻してから、残りを extend する
    conn_local_window_size_ += delta;
    int64_t take = std::min(conn_recv_reduction_, delta);
    conn_recv_reduction_ -= take;
    conn_recv_window_size_ += take;
    delta -= take;
    if (delta > 0) {
      (void)nghttp2_conn_extend_max_offset(conn_, static_cast<size_t>(delta));
    }
    return true;
  }

  // 存在しないストリームと負の stream_id は成功扱い (nghttp2 v1 と同じ)
  auto it = streams_.find(stream_id);
  if (it == streams_.end()) {
    return true;
  }

  int64_t& local = stream_local_window_size_[stream_id];
  int64_t& recv = stream_recv_window_size_[stream_id];
  int64_t& reduction = stream_recv_reduction_[stream_id];

  int64_t delta = static_cast<int64_t>(window_size) - local;
  if (delta == 0) {
    return true;
  }
  if (delta < 0) {
    local += delta;
    recv += delta;
    reduction -= delta;
    return true;
  }

  local += delta;
  int64_t take = std::min(reduction, delta);
  reduction -= take;
  recv += take;
  delta -= take;
  if (delta > 0) {
    (void)nghttp2_conn_extend_max_stream_offset(conn_, stream_id,
                                                static_cast<size_t>(delta));
  }
  return true;
}

// ========== メッセージング拡張 API ==========

bool Http2Connection::submit_trailer(
    int64_t stream_id,
    const std::vector<std::pair<std::string, std::string>>& headers) {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない。
  // nghttp2_conn_submit_trailers はストリーム ID を assert で検証するため
  // 上限とパリティ (クライアント起動ストリームは奇数) も弾く
  if (stream_id <= 0 || stream_id > INT32_MAX || (stream_id & 1) == 0) {
    return false;
  }

  // ストリームが存在しない (まだ受信していない・既に閉じた) 場合は送信
  // できない。ローカル側が half-closed (END_STREAM 送出済み) のストリーム
  // にもトレーラを送れない (RFC 9113 8.1 節)
  auto stream_it = streams_.find(stream_id);
  if (stream_it == streams_.end() || stream_it->second.local_closed) {
    return false;
  }

  // レスポンス (データプロバイダ) が設定されていないストリームには
  // トレーラを送信できない (submit_response が stream_buffers_ を作成する)
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

  // トレーラセクションは 1 つのみ (RFC 9113 8.1 節)
  if (trailers_submitted_.count(stream_id) != 0) {
    return false;
  }

  std::vector<nghttp2_nv> nva = bindings::to_nghttp2_nv(headers);
  if (nghttp2_conn_submit_trailers(conn_, stream_id, nva.data(), nva.size()) !=
      0) {
    return false;
  }

  trailers_submitted_.insert(stream_id);
  // 送信データが既に flush され read_data が WOULDBLOCK を返している場合に
  // EOF を渡してトレーラを送出させる
  nghttp2_conn_resume_stream(conn_, stream_id);
  return true;
}

bool Http2Connection::submit_priority_update(int64_t stream_id,
                                             uint32_t urgency,
                                             bool incremental) {
  if (!conn_ || closed_ || is_server_) {
    return false;
  }

  // stream_id 0 はコネクション全体を指す特別な値のため受け付けない。
  // クライアント起動ストリームは奇数
  if (stream_id <= 0 || stream_id > INT32_MAX || (stream_id & 1) == 0) {
    return false;
  }

  // RFC 9218 で定義された urgency の範囲 (0-7) を超える場合はガードする
  if (urgency > NGHTTP2_URGENCY_LOW) {
    return false;
  }

  // Priority field value (RFC 9218 6.3 節) をシリアライズする
  std::string field_value = "u=" + std::to_string(urgency);
  if (incremental) {
    field_value += ", i";
  }

  // nghttp2-v2 はストリームが存在しない場合に 0 を返して何も送出しない
  return nghttp2_conn_set_client_stream_priority(
             conn_, stream_id,
             reinterpret_cast<const uint8_t*>(field_value.data()),
             field_value.size()) == 0;
}

bool Http2Connection::change_extpri_stream_priority(int64_t stream_id,
                                                    uint32_t urgency,
                                                    bool incremental) {
  if (!conn_ || closed_ || !is_server_) {
    return false;
  }

  if (stream_id <= 0 || stream_id > INT32_MAX || (stream_id & 1) == 0) {
    return false;
  }

  if (urgency > NGHTTP2_URGENCY_LOW) {
    return false;
  }

  nghttp2_pri pri = {};
  pri.urgency = urgency;
  pri.inc = incremental ? 1 : 0;

  // nghttp2-v2 はストリームが存在しない場合に 0 を返して何もしない
  return nghttp2_conn_set_server_stream_priority(conn_, stream_id, &pri) == 0;
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
  auto* self = const_cast<Http2Connection*>(this);
  return self->ensure_pending_out();
}

bool Http2Connection::is_closed() const {
  return closed_;
}

// ========== セッション状態確認 API ==========

std::optional<std::map<std::string, uint32_t>>
Http2Connection::remote_settings() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  const nghttp2_proto_settings* settings =
      nghttp2_conn_get_remote_settings(conn_);
  if (settings == nullptr) {
    return std::nullopt;
  }

  // max_frame_size は nghttp2-v2 が SETTINGS を扱わず 16384 固定
  return std::map<std::string, uint32_t>{
      {"initial_window_size",
       static_cast<uint32_t>(settings->initial_max_stream_data)},
      {"max_concurrent_streams",
       static_cast<uint32_t>(settings->max_concurrent_streams)},
      {"max_frame_size", 16384},
      {"max_header_list_size",
       static_cast<uint32_t>(settings->max_field_section_size)},
  };
}

std::optional<std::map<std::string, uint32_t>> Http2Connection::local_settings()
    const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  const nghttp2_settings* settings = nghttp2_conn_get_settings(conn_);
  if (settings == nullptr) {
    return std::nullopt;
  }

  // max_frame_size は 16384 固定。max_header_list_size は nghttp2-v2 が
  // ローカルの上限を管理しないため上限なしを返す
  return std::map<std::string, uint32_t>{
      {"initial_window_size",
       static_cast<uint32_t>(settings->initial_max_stream_data)},
      {"max_concurrent_streams",
       static_cast<uint32_t>(settings->max_concurrent_streams_remote)},
      {"max_frame_size", 16384},
      {"max_header_list_size", 4294967295u},
  };
}

std::optional<int32_t> Http2Connection::local_window_size() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return static_cast<int32_t>(conn_local_window_size_ - conn_recv_window_size_);
}

std::optional<int32_t> Http2Connection::effective_recv_data_length() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return static_cast<int32_t>(std::max<int64_t>(0, conn_recv_window_size_));
}

std::optional<bool> Http2Connection::request_allowed() const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  if (is_server_) {
    return false;
  }

  if (goaway_sent_ || goaway_received_ || closing_) {
    return false;
  }

  // nghttp2-v2 では GOAWAY 受信後に > INT32_MAX が返る
  return nghttp2_conn_get_next_stream_id(conn_) <= INT32_MAX;
}

std::optional<int32_t> Http2Connection::stream_local_window_size(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  auto it = streams_.find(stream_id);
  if (it == streams_.end()) {
    return std::nullopt;
  }

  int64_t local = 0;
  auto local_it = stream_local_window_size_.find(stream_id);
  if (local_it != stream_local_window_size_.end()) {
    local = local_it->second;
  }

  int64_t recv = 0;
  auto recv_it = stream_recv_window_size_.find(stream_id);
  if (recv_it != stream_recv_window_size_.end()) {
    recv = recv_it->second;
  }

  return static_cast<int32_t>(std::max<int64_t>(0, local - recv));
}

std::optional<int32_t> Http2Connection::stream_effective_recv_data_length(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  if (streams_.find(stream_id) == streams_.end()) {
    return std::nullopt;
  }

  int64_t recv = 0;
  auto it = stream_recv_window_size_.find(stream_id);
  if (it != stream_recv_window_size_.end()) {
    recv = it->second;
  }
  return static_cast<int32_t>(std::max<int64_t>(0, recv));
}

std::optional<bool> Http2Connection::stream_local_close(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  auto it = streams_.find(stream_id);
  if (it == streams_.end()) {
    return std::nullopt;
  }
  return it->second.local_closed;
}

std::optional<bool> Http2Connection::stream_remote_close(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }

  auto it = streams_.find(stream_id);
  if (it == streams_.end()) {
    return std::nullopt;
  }
  return it->second.remote_closed;
}

// ========== ストリーム状態の追跡 ==========

void Http2Connection::register_stream(int64_t stream_id) {
  if (streams_.find(stream_id) == streams_.end()) {
    streams_[stream_id] = StreamRuntime{};
  }
  if (stream_local_window_size_.find(stream_id) ==
      stream_local_window_size_.end()) {
    stream_local_window_size_[stream_id] = config_.initial_window_size;
  }
}

void Http2Connection::unregister_stream(int64_t stream_id) {
  streams_.erase(stream_id);
  stream_buffers_.erase(stream_id);
  stream_data_sent_.erase(stream_id);
  trailers_submitted_.erase(stream_id);
  stream_local_window_size_.erase(stream_id);
  stream_recv_window_size_.erase(stream_id);
  stream_recv_reduction_.erase(stream_id);
  pending_headers_.erase(stream_id);
  pending_trailers_.erase(stream_id);
}

void Http2Connection::push_event(Http2Event event) {
  events_.push_back(std::move(event));
}

void Http2Connection::extend_recv_connection_window(size_t size) {
  conn_recv_window_size_ += static_cast<int64_t>(size);

  // v1 の nghttp2_should_send_window_update と同じく、受信ウィンドウの
  // 半分以上を受け取ったら WINDOW_UPDATE を送る
  if (conn_recv_window_size_ > 0 &&
      conn_recv_window_size_ >= conn_local_window_size_ / 2) {
    int64_t amount = conn_recv_window_size_;
    conn_recv_window_size_ = 0;
    (void)nghttp2_conn_extend_max_offset(conn_, static_cast<size_t>(amount));
  }
}

void Http2Connection::extend_recv_stream_window(int64_t stream_id,
                                                size_t size) {
  auto local_it = stream_local_window_size_.find(stream_id);
  if (local_it == stream_local_window_size_.end()) {
    stream_local_window_size_[stream_id] = config_.initial_window_size;
    local_it = stream_local_window_size_.find(stream_id);
  }

  int64_t& recv = stream_recv_window_size_[stream_id];
  recv += static_cast<int64_t>(size);

  if (recv > 0 && recv >= local_it->second / 2) {
    int64_t amount = recv;
    recv = 0;
    (void)nghttp2_conn_extend_max_stream_offset(conn_, stream_id,
                                                static_cast<size_t>(amount));
  }
}

// ========== nghttp2-v2 コールバック ==========

int Http2Connection::on_stream_open_callback(nghttp2_conn* conn,
                                             int64_t stream_id,
                                             void* user_data) noexcept {
  (void)conn;
  auto* self = static_cast<Http2Connection*>(user_data);
  self->register_stream(stream_id);
  return 0;
}

int Http2Connection::on_stream_close_callback(nghttp2_conn* conn,
                                              uint32_t flags,
                                              int64_t stream_id,
                                              uint32_t error_code,
                                              void* user_data,
                                              void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  // 受信した RST_STREAM によるクローズだけを StreamReset として通知する。
  // ローカルから shutdown_stream した場合は local_resets_ で判別する
  if ((flags & NGHTTP2_STREAM_CLOSE_FLAG_ERROR_CODE_SET) &&
      self->local_resets_.count(stream_id) == 0) {
    Http2Event event;
    event.type = Http2EventType::StreamReset;
    event.stream_id = stream_id;
    event.error_code = error_code;
    self->push_event(std::move(event));
  }

  self->local_resets_.erase(stream_id);
  self->unregister_stream(stream_id);
  return 0;
}

int Http2Connection::on_recv_settings_entry_callback(
    nghttp2_conn* conn,
    const nghttp2_settings_entry* ent,
    void* user_data) noexcept {
  (void)conn;
  (void)ent;
  (void)user_data;
  // 個々の SETTINGS エントリは公開 API で観測しない
  return 0;
}

int Http2Connection::on_recv_settings_callback(
    nghttp2_conn* conn,
    const nghttp2_proto_settings* settings,
    void* user_data) noexcept {
  (void)conn;
  (void)settings;
  auto* self = static_cast<Http2Connection*>(user_data);

  Http2Event event;
  event.type = Http2EventType::Settings;
  self->push_event(std::move(event));
  return 0;
}

int Http2Connection::on_recv_settings_ack_callback(nghttp2_conn* conn,
                                                   void* user_data) noexcept {
  (void)conn;
  (void)user_data;
  // ACK は公開 API で観測しない
  return 0;
}

int Http2Connection::on_begin_headers_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);
  self->pending_headers_[stream_id].clear();
  return 0;
}

int Http2Connection::on_recv_header_callback(nghttp2_conn* conn,
                                             int64_t stream_id,
                                             int32_t token,
                                             nghttp2_rcbuf* name,
                                             nghttp2_rcbuf* value,
                                             uint8_t flags,
                                             void* user_data,
                                             void* stream_user_data) noexcept {
  (void)conn;
  (void)token;
  (void)flags;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  nghttp2_vec name_buf = nghttp2_rcbuf_get_buf(name);
  nghttp2_vec value_buf = nghttp2_rcbuf_get_buf(value);
  std::string name_str(reinterpret_cast<const char*>(name_buf.base),
                       name_buf.len);
  std::string value_str(reinterpret_cast<const char*>(value_buf.base),
                        value_buf.len);
  self->pending_headers_[stream_id].emplace_back(std::move(name_str),
                                                 std::move(value_str));
  return 0;
}

namespace {

// ヘッダーセクションを Headers / Informational / Trailers に分類する
//
// nghttp2-v2 はトレーラを end_trailers で分離するため、ここでは
// リクエスト / レスポンス / 1xx 中間応答のみを扱う。擬似ヘッダーで判定する:
//   - `:method` を持つものはリクエスト (RFC 9113 Section 8.3)
//   - `:status` を持つものはレスポンス。1xx なら interim response
//   - どちらも持たないものはトレーラ (RFC 9110 Section 6.5)
Http2EventType classify_headers_event(
    const std::vector<std::pair<std::string, std::string>>& headers) {
  bool has_status = false;
  bool informational = false;
  for (const auto& [name, value] : headers) {
    if (name == ":method") {
      return Http2EventType::Headers;
    }
    if (name != ":status") {
      continue;
    }
    has_status = true;
    informational = !value.empty() && value[0] == '1';
  }
  if (!has_status) {
    return Http2EventType::Trailers;
  }
  return informational ? Http2EventType::Informational
                       : Http2EventType::Headers;
}

}  // namespace

int Http2Connection::on_end_headers_callback(nghttp2_conn* conn,
                                             int64_t stream_id,
                                             int fin,
                                             void* user_data,
                                             void* stream_user_data) noexcept {
  (void)conn;
  (void)fin;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  auto it = self->pending_headers_.find(stream_id);
  if (it != self->pending_headers_.end()) {
    Http2Event event;
    event.stream_id = stream_id;
    event.headers = std::move(it->second);
    event.type = classify_headers_event(event.headers);
    self->push_event(std::move(event));
    self->pending_headers_.erase(it);
  }
  return 0;
}

int Http2Connection::on_begin_trailers_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);
  self->pending_trailers_[stream_id].clear();
  return 0;
}

int Http2Connection::on_recv_trailer_callback(nghttp2_conn* conn,
                                              int64_t stream_id,
                                              int32_t token,
                                              nghttp2_rcbuf* name,
                                              nghttp2_rcbuf* value,
                                              uint8_t flags,
                                              void* user_data,
                                              void* stream_user_data) noexcept {
  (void)conn;
  (void)token;
  (void)flags;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  nghttp2_vec name_buf = nghttp2_rcbuf_get_buf(name);
  nghttp2_vec value_buf = nghttp2_rcbuf_get_buf(value);
  std::string name_str(reinterpret_cast<const char*>(name_buf.base),
                       name_buf.len);
  std::string value_str(reinterpret_cast<const char*>(value_buf.base),
                        value_buf.len);
  self->pending_trailers_[stream_id].emplace_back(std::move(name_str),
                                                  std::move(value_str));
  return 0;
}

int Http2Connection::on_end_trailers_callback(nghttp2_conn* conn,
                                              int64_t stream_id,
                                              int fin,
                                              void* user_data,
                                              void* stream_user_data) noexcept {
  (void)conn;
  (void)fin;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  auto it = self->pending_trailers_.find(stream_id);
  if (it != self->pending_trailers_.end()) {
    Http2Event event;
    event.type = Http2EventType::Trailers;
    event.stream_id = stream_id;
    event.headers = std::move(it->second);
    self->push_event(std::move(event));
    self->pending_trailers_.erase(it);
  }
  return 0;
}

int Http2Connection::on_recv_data_callback(nghttp2_conn* conn,
                                           int64_t stream_id,
                                           const uint8_t* data,
                                           size_t datalen,
                                           void* user_data,
                                           void* stream_user_data) noexcept {
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  // nghttp2-v2 では受信ウィンドウの管理はアプリの責務。v1 相当の自動
  // WINDOW_UPDATE を実装する
  self->extend_recv_stream_window(stream_id, datalen);
  self->extend_recv_connection_window(datalen);

  Http2Event event;
  event.type = Http2EventType::Data;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + datalen);
  self->push_event(std::move(event));

  (void)conn;
  return 0;
}

int Http2Connection::on_remote_end_stream_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  auto it = self->streams_.find(stream_id);
  if (it != self->streams_.end()) {
    it->second.remote_closed = true;
  }

  Http2Event event;
  event.type = Http2EventType::StreamEnd;
  event.stream_id = stream_id;
  self->push_event(std::move(event));
  return 0;
}

int Http2Connection::on_local_end_stream_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  auto it = self->streams_.find(stream_id);
  if (it != self->streams_.end()) {
    it->second.local_closed = true;
  }
  return 0;
}

int Http2Connection::on_recv_ping_ack_callback(nghttp2_conn* conn,
                                               const nghttp2_ping_data* data,
                                               nghttp2_duration rtt,
                                               void* user_data) noexcept {
  (void)conn;
  auto* self = static_cast<Http2Connection*>(user_data);

  Http2Event event;
  event.type = Http2EventType::Ping;
  event.opaque_data.assign(data->data, data->data + sizeof(data->data));
  event.ping_rtt_ns = rtt;
  self->push_event(std::move(event));
  return 0;
}

int Http2Connection::on_shutdown_callback(nghttp2_conn* conn,
                                          int64_t last_stream_id,
                                          uint32_t error_code,
                                          void* user_data) noexcept {
  (void)conn;
  auto* self = static_cast<Http2Connection*>(user_data);

  Http2Event event;
  event.type = Http2EventType::GoAway;
  event.error_code = error_code;
  event.last_stream_id = last_stream_id;
  self->push_event(std::move(event));

  // RFC 9113 Section 6.8 の graceful shutdown: GOAWAY 受信後も既存
  // ストリームの送受信を継続する。closed_ にはせず、新規ストリーム
  // 開始の抑止のみを goaway_received_ で行う
  self->goaway_received_ = true;
  return 0;
}

nghttp2_ssize Http2Connection::on_read_data_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    nghttp2_vec* vec,
    size_t veccnt,
    uint32_t* pflags,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)veccnt;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  if (vec == nullptr || veccnt == 0) {
    return NGHTTP2_ERR_CALLBACK_FAILURE;
  }

  auto it = self->stream_buffers_.find(stream_id);
  for (;;) {
    if (it != self->stream_buffers_.end() && !it->second.empty()) {
      StreamData& front = it->second.front();
      size_t remaining = front.data.size() - front.offset;
      if (remaining == 0) {
        if (front.eof) {
          // 空の END_STREAM。バッファを返さず EOF だけ通知する
          *pflags |= NGHTTP2_READ_DATA_FLAG_EOF;
          it->second.pop_front();
          vec[0] = nghttp2_vec{nullptr, 0};
          return 1;
        }
        // 空データ (eof なし) は送出するものが無いため捨てる
        it->second.pop_front();
        continue;
      }

      // アプリのバッファを直接指す (nghttp2-v2 は書き込み確認まで
      // アプリがバッファを保持する契約)
      vec[0] = nghttp2_vec{
          const_cast<uint8_t*>(front.data.data() + front.offset), remaining};
      if (front.eof) {
        *pflags |= NGHTTP2_READ_DATA_FLAG_EOF;
      }
      return 1;
    }

    // 送信データが尽きた。トレーラを送信済みなら EOF を渡して
    // トレーラ HEADERS を送出させる
    if (self->trailers_submitted_.count(stream_id) != 0) {
      *pflags |= NGHTTP2_READ_DATA_FLAG_EOF;
      vec[0] = nghttp2_vec{nullptr, 0};
      return 1;
    }

    return NGHTTP2_ERR_WOULDBLOCK;
  }
}

int Http2Connection::on_write_stream_data_offset_callback(
    nghttp2_conn* conn,
    int64_t stream_id,
    uint64_t offset,
    size_t len,
    void* user_data,
    void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<Http2Connection*>(user_data);

  uint64_t& sent = self->stream_data_sent_[stream_id];
  uint64_t new_sent = offset + len;
  if (new_sent <= sent) {
    return 0;
  }

  uint64_t delta = new_sent - sent;
  sent = new_sent;

  auto it = self->stream_buffers_.find(stream_id);
  if (it == self->stream_buffers_.end()) {
    return 0;
  }

  while (delta > 0 && !it->second.empty()) {
    StreamData& front = it->second.front();
    size_t remaining = front.data.size() - front.offset;
    size_t take = std::min<size_t>(static_cast<size_t>(delta), remaining);
    front.offset += take;
    delta -= take;
    if (front.offset >= front.data.size()) {
      it->second.pop_front();
    }
  }

  return 0;
}

void Http2Connection::on_rand_callback(uint8_t* dest, size_t destlen) noexcept {
  // rand は接続生成時にレートリミッタの種として 1 度だけ呼ばれる
  std::random_device rd;
  std::uniform_int_distribution<unsigned int> dist(0, 255);
  for (size_t i = 0; i < destlen; ++i) {
    dest[i] = static_cast<uint8_t>(dist(rd));
  }
}

// ========== Python バインディング ==========

namespace {

std::string get_nghttp2_version() {
  // nghttp2-v2 はバージョン取得関数を公開していないため、コンパイル時に
  // version.h へ展開されたマクロを使う
  return NGHTTP2_VERSION;
}

// ALPN プロトコルリストから h2 / http/1.1 を選択する
//
// RFC 7301 のプロトコル名は 1-255 バイト。サーバー優先度は h2 > http/1.1
std::optional<std::string> select_alpn(
    const std::vector<std::string>& client_protocols) {
  auto has_protocol = [&client_protocols](const char* name) {
    for (const auto& proto : client_protocols) {
      if (proto.size() > 255) {
        continue;
      }
      if (proto == name) {
        return true;
      }
    }
    return false;
  };

  if (has_protocol("h2")) {
    return std::string("h2");
  }
  if (has_protocol("http/1.1")) {
    return std::string("http/1.1");
  }
  return std::nullopt;
}

}  // namespace

void bind_http2(nb::module_& m) {
  auto http2_m = m.def_submodule("http2", "HTTP/2 protocol (nghttp2-v2)");

  // Http2Config のバインディング
  nb::class_<Http2Config>(http2_m, "Config", "HTTP/2 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("initial_window_size", &Http2Config::initial_window_size,
              "初期ウィンドウサイズ")
      .def_rw("max_concurrent_streams", &Http2Config::max_concurrent_streams,
              "最大同時ストリーム数")
      .def_rw("max_frame_size", &Http2Config::max_frame_size,
              "最大フレームサイズ (nghttp2-v2 では 16384 固定)")
      .def_rw("max_header_list_size", &Http2Config::max_header_list_size,
              "最大ヘッダーリストサイズ")
      .def_rw("is_server", &Http2Config::is_server, "サーバーモード")
      .def_rw("no_rfc7540_priorities", &Http2Config::no_rfc7540_priorities,
              "SETTINGS_NO_RFC7540_PRIORITIES を送信するか "
              "(nghttp2-v2 は常に送信するため設定は無効)");

  // Http2EventType のバインディング
  nb::enum_<Http2EventType>(http2_m, "EventType", "HTTP/2 イベント種別")
      .value("HEADERS", Http2EventType::Headers)
      .value("DATA", Http2EventType::Data)
      .value("STREAM_END", Http2EventType::StreamEnd)
      .value("STREAM_RESET", Http2EventType::StreamReset)
      .value("GO_AWAY", Http2EventType::GoAway)
      .value("SETTINGS", Http2EventType::Settings)
      .value("PING", Http2EventType::Ping)
      .value("INFORMATIONAL", Http2EventType::Informational)
      .value("TRAILERS", Http2EventType::Trailers);

  // Http2Event のバインディング
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
      .def_prop_ro(
          "opaque_data",
          [](const Http2Event& e) {
            return nb::bytes(
                reinterpret_cast<const char*>(e.opaque_data.data()),
                e.opaque_data.size());
          },
          "PING ACK の opaque data (RFC 9113 Section 6.7)")
      .def_ro("ping_rtt_ns", &Http2Event::ping_rtt_ns,
              "PING ACK の RTT (ナノ秒)");

  // Http2Connection のバインディング
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
            bindings::check_python_input_size("receive data", data.size());
            return self.receive(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::lock_self(), nb::arg("data"),
          nb::sig("def receive(self, data: bytes) -> int"),
          "受信したデータを処理 (data が 1 MiB 超の場合は ValueError)")
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
          nb::lock_self(), nb::sig("def send(self) -> bytes | None"),
          "送信すべきデータを取得")
      .def("submit_request", &Http2Connection::submit_request, nb::lock_self(),
           nb::arg("headers"),
           nb::sig("def submit_request(self, headers: list[tuple[str, str]]) "
                   "-> int"),
           "リクエストを送信 (終端は send_data の eof=True で行う)")
      .def("submit_response", &Http2Connection::submit_response,
           nb::lock_self(), nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_response(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> None"),
           "レスポンスを送信")
      .def(
          "send_data",
          [](Http2Connection& self, int64_t stream_id, nb::bytes data,
             bool eof) {
            bindings::check_python_input_size("send_data data", data.size());
            self.send_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                eof);
          },
          nb::lock_self(), nb::arg("stream_id"), nb::arg("data"),
          nb::arg("eof") = false,
          nb::sig("def send_data(self, stream_id: int, data: bytes, eof: bool "
                  "= False) -> None"),
          "ストリームにデータを送信 (data が 1 MiB 超の場合は ValueError)")
      .def("reset_stream", &Http2Connection::reset_stream, nb::lock_self(),
           nb::arg("stream_id"), nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "ストリームをリセット")
      .def("goaway", &Http2Connection::goaway, nb::lock_self(),
           nb::arg("error_code") = 0,
           nb::sig("def goaway(self, error_code: int = 0) -> None"),
           "GOAWAY を送信して graceful shutdown を開始")
      .def("_test_force_close", &Http2Connection::test_force_close,
           nb::lock_self(),
           "テスト専用: 低レベルを閉鎖状態にする (production からは呼ばない)")
      .def(
          "_test_stream_buffer_count",
          &Http2Connection::test_stream_buffer_count, nb::lock_self(),
          nb::arg("stream_id"),
          nb::sig("def _test_stream_buffer_count(self, stream_id: int) -> int"),
          "テスト専用: 送信バッファのエントリ数を返す")
      .def(
          "_test_stream_buffer_remaining",
          &Http2Connection::test_stream_buffer_remaining, nb::lock_self(),
          nb::arg("stream_id"),
          nb::sig(
              "def _test_stream_buffer_remaining(self, stream_id: int) -> int"),
          "テスト専用: 送信バッファ先頭の残バイト数を返す")
      .def("_test_stream_buffer_offset",
           &Http2Connection::test_stream_buffer_offset, nb::lock_self(),
           nb::arg("stream_id"),
           nb::sig(
               "def _test_stream_buffer_offset(self, stream_id: int) -> int"),
           "テスト専用: 送信バッファ先頭の送出確認済みオフセットを返す")
      .def(
          "ping",
          [](Http2Connection& self, nb::bytes opaque_data) {
            bindings::check_python_input_size("ping opaque_data",
                                              opaque_data.size());
            self.ping(std::vector<uint8_t>(
                opaque_data.c_str(), opaque_data.c_str() + opaque_data.size()));
          },
          nb::lock_self(), nb::arg("opaque_data") = nb::bytes("", 0),
          nb::sig("def ping(self, opaque_data: bytes = b'') -> None"),
          "PING を送信 (opaque_data は 8 バイト固定。1 MiB 超の場合は "
          "ValueError)")
      .def("terminate_session", &Http2Connection::terminate_session,
           nb::lock_self(), nb::arg("error_code") = 0,
           nb::arg("last_stream_id") = 0,
           nb::sig("def terminate_session(self, error_code: int = 0, "
                   "last_stream_id: int = 0) -> bool"),
           "GOAWAY を送信してセッションを即時終了")
      .def("set_local_window_size", &Http2Connection::set_local_window_size,
           nb::lock_self(), nb::arg("stream_id"), nb::arg("window_size"),
           nb::sig("def set_local_window_size(self, stream_id: int, "
                   "window_size: int) -> bool"),
           "ローカルウィンドウサイズを動的に変更")
      .def("submit_trailer", &Http2Connection::submit_trailer, nb::lock_self(),
           nb::arg("stream_id"), nb::arg("headers"),
           nb::sig("def submit_trailer(self, stream_id: int, headers: "
                   "list[tuple[str, str]]) -> bool"),
           "トレーラを送信")
      .def("submit_priority_update", &Http2Connection::submit_priority_update,
           nb::lock_self(), nb::arg("stream_id"), nb::arg("urgency"),
           nb::arg("incremental"),
           nb::sig("def submit_priority_update(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "PRIORITY_UPDATE フレームを送信")
      .def("change_extpri_stream_priority",
           &Http2Connection::change_extpri_stream_priority, nb::lock_self(),
           nb::arg("stream_id"), nb::arg("urgency"), nb::arg("incremental"),
           nb::sig("def change_extpri_stream_priority(self, stream_id: int, "
                   "urgency: int, incremental: bool) -> bool"),
           "ストリームの優先度を変更")
      .def("next_event", &Http2Connection::next_event, nb::lock_self(),
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("want_write", &Http2Connection::want_write, nb::lock_self(),
           nb::sig("def want_write(self) -> bool"), "送信待ちデータがあるか")
      .def("is_closed", &Http2Connection::is_closed, nb::lock_self(),
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def_prop_ro(
          "remote_settings", &Http2Connection::remote_settings, nb::lock_self(),
          nb::sig("def remote_settings(self) -> dict[str, int] | None"),
          "ピアの SETTINGS の値を取得")
      .def_prop_ro("local_settings", &Http2Connection::local_settings,
                   nb::lock_self(),
                   nb::sig("def local_settings(self) -> dict[str, int] | None"),
                   "ローカルの SETTINGS の値を取得")
      .def_prop_ro("local_window_size", &Http2Connection::local_window_size,
                   nb::lock_self(),
                   nb::sig("def local_window_size(self) -> int | None"),
                   "コネクションのローカルウィンドウ残量を取得")
      .def_prop_ro(
          "effective_recv_data_length",
          &Http2Connection::effective_recv_data_length, nb::lock_self(),
          nb::sig("def effective_recv_data_length(self) -> int | None"),
          "WINDOW_UPDATE 未送信の受信 DATA バイト数を取得")
      .def_prop_ro("request_allowed", &Http2Connection::request_allowed,
                   nb::lock_self(),
                   nb::sig("def request_allowed(self) -> bool | None"),
                   "新しいリクエストを送信できるかを取得")
      .def("stream_local_window_size",
           &Http2Connection::stream_local_window_size, nb::lock_self(),
           nb::arg("stream_id"),
           nb::sig("def stream_local_window_size(self, stream_id: int) -> "
                   "int | None"),
           "ストリームのローカルウィンドウ残量を取得")
      .def("stream_effective_recv_data_length",
           &Http2Connection::stream_effective_recv_data_length, nb::lock_self(),
           nb::arg("stream_id"),
           nb::sig("def stream_effective_recv_data_length(self, stream_id: "
                   "int) -> int | None"),
           "ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数を取得")
      .def("stream_local_close", &Http2Connection::stream_local_close,
           nb::lock_self(), nb::arg("stream_id"),
           nb::sig(
               "def stream_local_close(self, stream_id: int) -> bool | None"),
           "ストリームのローカル側が half-closed かを取得")
      .def("stream_remote_close", &Http2Connection::stream_remote_close,
           nb::lock_self(), nb::arg("stream_id"),
           nb::sig(
               "def stream_remote_close(self, stream_id: int) -> bool | None"),
           "ストリームのリモート側が half-closed かを取得");

  // nghttp2 バージョン情報
  http2_m.def("get_version", &get_nghttp2_version,
              nb::sig("def get_version() -> str"),
              "nghttp2 のバージョンを取得");

  // ALPN プロトコル選択 (サーバー用ユーティリティ)
  http2_m.def("select_alpn", &select_alpn, nb::arg("client_protocols"),
              nb::sig("def select_alpn(client_protocols: list[str]) -> "
                      "str | None"),
              "ALPN プロトコルを選択 (h2 / http/1.1 の優先順)");
}

}  // namespace http2
}  // namespace webtransport
