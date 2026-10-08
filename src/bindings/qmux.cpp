#include "bindings/qmux.h"

#include <chrono>
#include <cstring>
#include <random>

#include <dwnx/version.h>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

namespace nb = nanobind;

namespace webtransport::qmux {

namespace {

/**
 * 現在時刻をナノ秒で返す (dwnx のタイマーは単調増加のナノ秒)
 */
uint64_t now_ns() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

/**
 * 乱数を供給する (dwnx_rand は user_data を受け取らないため無名名前空間の関数にする)
 */
void rand_cb(uint8_t* dest, size_t destlen) noexcept {
  static thread_local std::mt19937_64 engine(std::random_device{}());
  static thread_local std::uniform_int_distribution<uint64_t> dist;
  size_t offset = 0;
  while (offset < destlen) {
    const uint64_t value = dist(engine);
    const size_t chunk = std::min(sizeof(value), destlen - offset);
    std::memcpy(dest + offset, &value, chunk);
    offset += chunk;
  }
}

}  // namespace

/**
 * ピアのトランスポートパラメータを受信した
 */
int recv_transport_params_cb(dwnx_conn* conn,
                             const dwnx_transport_params* params,
                             void* user_data) noexcept {
  (void)conn;
  (void)params;
  auto* self = static_cast<QmuxConnection*>(user_data);
  QmuxEvent event;
  event.type = QmuxEventType::TransportParamsReceived;
  self->push_event(std::move(event));
  return 0;
}

/**
 * ストリームデータを受信した
 */
int recv_stream_data_cb(dwnx_conn* conn,
                        uint32_t flags,
                        int64_t stream_id,
                        uint64_t offset,
                        const uint8_t* data,
                        size_t datalen,
                        void* user_data,
                        void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<QmuxConnection*>(user_data);
  QmuxEvent event;
  event.type = QmuxEventType::StreamData;
  event.stream_id = stream_id;
  event.offset = offset;
  event.data.assign(data, data + datalen);
  event.fin = (flags & DWNX_STREAM_DATA_FLAG_FIN) != 0;
  self->push_event(std::move(event));
  return 0;
}

/**
 * ストリームが閉じた
 */
int stream_close_cb(dwnx_conn* conn,
                    uint32_t flags,
                    int64_t stream_id,
                    uint64_t rx_app_error_code,
                    uint64_t tx_app_error_code,
                    void* user_data,
                    void* stream_user_data) noexcept {
  (void)conn;
  (void)flags;
  (void)tx_app_error_code;
  (void)stream_user_data;
  auto* self = static_cast<QmuxConnection*>(user_data);
  QmuxEvent event;
  event.type = QmuxEventType::StreamClosed;
  event.stream_id = stream_id;
  // 受信側のアプリケーションエラーコードを通知する
  event.error_code = rx_app_error_code;
  self->push_event(std::move(event));
  return 0;
}

/**
 * ストリームがリセットされた
 */
int stream_reset_cb(dwnx_conn* conn,
                    int64_t stream_id,
                    uint64_t final_size,
                    uint64_t app_error_code,
                    void* user_data,
                    void* stream_user_data) noexcept {
  (void)conn;
  (void)final_size;
  (void)stream_user_data;
  auto* self = static_cast<QmuxConnection*>(user_data);
  QmuxEvent event;
  event.type = QmuxEventType::StreamReset;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));
  return 0;
}

/**
 * 送信の停止を要求された
 */
int recv_stop_sending_cb(dwnx_conn* conn,
                         int64_t stream_id,
                         uint64_t app_error_code,
                         void* user_data,
                         void* stream_user_data) noexcept {
  (void)conn;
  (void)stream_user_data;
  auto* self = static_cast<QmuxConnection*>(user_data);
  QmuxEvent event;
  event.type = QmuxEventType::StopSending;
  event.stream_id = stream_id;
  event.error_code = app_error_code;
  self->push_event(std::move(event));
  return 0;
}

std::unique_ptr<QmuxConnection> QmuxConnection::create_client(
    const QmuxConfig& config) {
  auto connection = std::unique_ptr<QmuxConnection>(new QmuxConnection());
  connection->initialize(config, false);
  return connection;
}

std::unique_ptr<QmuxConnection> QmuxConnection::create_server(
    const QmuxConfig& config) {
  auto connection = std::unique_ptr<QmuxConnection>(new QmuxConnection());
  connection->initialize(config, true);
  return connection;
}

void QmuxConnection::initialize(const QmuxConfig& config, bool is_server) {
  dwnx_settings settings;
  dwnx_settings_default(&settings);
  settings.initial_ts = now_ns();
  settings.conn_id = static_cast<uint64_t>(std::random_device{}());

  dwnx_transport_params params;
  dwnx_transport_params_default(&params);
  params.initial_max_streams_bidi = config.initial_max_streams_bidi;
  params.initial_max_streams_uni = config.initial_max_streams_uni;
  params.initial_max_data = config.initial_max_data;
  params.initial_max_stream_data_bidi_local =
      config.initial_max_stream_data_bidi_local;
  params.initial_max_stream_data_bidi_remote =
      config.initial_max_stream_data_bidi_remote;
  params.initial_max_stream_data_uni = config.initial_max_stream_data_uni;
  params.max_idle_timeout = config.max_idle_timeout_ns;
  params.max_record_size = config.max_record_size;

  dwnx_callbacks callbacks{};
  callbacks.rand = rand_cb;
  callbacks.recv_transport_params = recv_transport_params_cb;
  callbacks.recv_stream_data = recv_stream_data_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.stream_reset = stream_reset_cb;
  callbacks.recv_stop_sending = recv_stop_sending_cb;

  // dwnx_mem_default() は静的な既定アロケータを指すポインタを返す
  const dwnx_mem* mem = dwnx_mem_default();

  int rv = 0;
  if (is_server) {
    rv =
        dwnx_conn_server_new(&conn_, &callbacks, &settings, &params, mem, this);
  } else {
    rv =
        dwnx_conn_client_new(&conn_, &callbacks, &settings, &params, mem, this);
  }
  if (rv != 0) {
    conn_ = nullptr;
    throw std::runtime_error("failed to create QMux connection: " +
                             strerror(rv));
  }

  write_buffer_.resize(static_cast<size_t>(config.max_record_size) + 4096);
}

QmuxConnection::~QmuxConnection() {
  if (conn_ != nullptr) {
    dwnx_conn_del(conn_);
    conn_ = nullptr;
  }
}

int QmuxConnection::receive(const uint8_t* data, size_t datalen) {
  if (conn_ == nullptr) {
    return DWNX_ERR_INVALID_ARGUMENT;
  }
  return dwnx_conn_read(conn_, data, datalen, now_ns());
}

std::optional<std::vector<uint8_t>>
QmuxConnection::write_pending_stream_data() {
  // dwnx の契約: 0 か正値が返るまで呼び続ける。DWNX_ERR_WRITE_MORE は「同じ
  // レコードにまだ詰められる」、DWNX_ERR_STREAM_DATA_BLOCKED /
  // DWNX_ERR_STREAM_SHUT_WR は「このストリームには今は書けない」を表す
  for (int attempt = 0; attempt < 64; ++attempt) {
    int64_t stream_id = -1;
    dwnx_vec vec{};
    size_t vec_count = 0;
    uint32_t flags = DWNX_WRITE_STREAM_FLAG_NONE;
    if (!pending_stream_data_.empty()) {
      auto& head = pending_stream_data_.front();
      stream_id = head.stream_id;
      vec.base = head.data.data();
      vec.len = head.data.size();
      vec_count = 1;
      if (head.fin) {
        flags |= DWNX_WRITE_STREAM_FLAG_FIN;
      }
    }

    dwnx_ssize datalen = -1;
    const dwnx_ssize n = dwnx_conn_writev_stream(
        conn_, write_buffer_.data(), write_buffer_.size(), &datalen, flags,
        stream_id, vec_count == 0 ? nullptr : &vec, vec_count, now_ns());

    if (n > 0) {
      // 1 レコード分が完成した。エンコード済みのストリームデータを消費する
      consume_stream_data(datalen);
      return std::vector<uint8_t>(write_buffer_.begin(),
                                  write_buffer_.begin() + n);
    }

    if (n == DWNX_ERR_WRITE_MORE) {
      // 同じレコードに追記できる。エンコード済みの分だけ消費して続ける
      consume_stream_data(datalen);
      continue;
    }

    if (n == DWNX_ERR_STREAM_DATA_BLOCKED || n == DWNX_ERR_STREAM_SHUT_WR) {
      // フロー制御で送れない、または送信側が閉じている。このデータは送れない
      // ため破棄して次のデータへ進む
      if (!pending_stream_data_.empty()) {
        pending_stream_data_.pop_front();
      }
      continue;
    }

    // 0 (送るものなし) とその他のエラー
    return std::nullopt;
  }
  return std::nullopt;
}

void QmuxConnection::consume_stream_data(dwnx_ssize datalen) {
  if (datalen <= 0 || pending_stream_data_.empty()) {
    return;
  }
  auto& head = pending_stream_data_.front();
  const auto consumed = static_cast<size_t>(datalen);
  if (consumed >= head.data.size()) {
    pending_stream_data_.pop_front();
    return;
  }
  head.data.erase(head.data.begin(), head.data.begin() + consumed);
}

std::optional<std::vector<uint8_t>> QmuxConnection::write_connection_close() {
  const uint64_t ts = now_ns();
  const dwnx_ssize n = dwnx_conn_write_connection_close(
      conn_, write_buffer_.data(), write_buffer_.size(), &close_ccerr_, ts);
  if (n <= 0) {
    return std::nullopt;
  }
  return std::vector<uint8_t>(write_buffer_.begin(), write_buffer_.begin() + n);
}

std::optional<std::vector<uint8_t>> QmuxConnection::send() {
  if (conn_ == nullptr) {
    return std::nullopt;
  }
  if (close_requested_) {
    close_requested_ = false;
    return write_connection_close();
  }
  if (auto record = write_pending_stream_data()) {
    return record;
  }
  const uint64_t ts = now_ns();
  const dwnx_ssize n = dwnx_conn_write_record(conn_, write_buffer_.data(),
                                              write_buffer_.size(), ts);
  if (n <= 0) {
    return std::nullopt;
  }
  return std::vector<uint8_t>(write_buffer_.begin(), write_buffer_.begin() + n);
}

std::optional<uint64_t> QmuxConnection::get_timeout() const {
  if (conn_ == nullptr) {
    return std::nullopt;
  }
  const uint64_t expiry = dwnx_conn_get_expiry(conn_);
  if (expiry == UINT64_MAX) {
    return std::nullopt;
  }
  const uint64_t ts = now_ns();
  if (expiry <= ts) {
    return 0;
  }
  return expiry - ts;
}

void QmuxConnection::handle_timeout() {
  if (conn_ == nullptr) {
    return;
  }
  dwnx_conn_handle_expiry(conn_, now_ns());
}

int64_t QmuxConnection::open_stream(bool bidirectional) {
  if (conn_ == nullptr) {
    return -1;
  }
  int64_t stream_id = -1;
  const int rv = bidirectional
                     ? dwnx_conn_open_bidi_stream(conn_, &stream_id, nullptr)
                     : dwnx_conn_open_uni_stream(conn_, &stream_id, nullptr);
  if (rv != 0) {
    return -1;
  }
  return stream_id;
}

void QmuxConnection::send_stream_data(int64_t stream_id,
                                      const uint8_t* data,
                                      size_t datalen,
                                      bool fin) {
  PendingStreamData pending;
  pending.stream_id = stream_id;
  pending.data.assign(data, data + datalen);
  pending.fin = fin;
  pending_stream_data_.push_back(std::move(pending));
}

void QmuxConnection::close(uint64_t error_code, const std::string& reason) {
  if (conn_ == nullptr) {
    return;
  }
  dwnx_ccerr_default(&close_ccerr_);
  dwnx_ccerr_set_application_error(
      &close_ccerr_, error_code,
      reinterpret_cast<const uint8_t*>(reason.data()), reason.size());
  close_requested_ = true;
}

std::optional<QmuxEvent> QmuxConnection::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }
  QmuxEvent event = std::move(events_.front());
  events_.pop_front();
  return event;
}

bool QmuxConnection::is_server() const {
  return conn_ != nullptr && dwnx_conn_is_server(conn_) != 0;
}

uint64_t QmuxConnection::streams_bidi_left() const {
  return conn_ == nullptr ? 0 : dwnx_conn_get_streams_bidi_left(conn_);
}

uint64_t QmuxConnection::streams_uni_left() const {
  return conn_ == nullptr ? 0 : dwnx_conn_get_streams_uni_left(conn_);
}

std::string QmuxConnection::strerror(int liberr) {
  const char* message = dwnx_strerror(liberr);
  return message == nullptr ? std::string() : std::string(message);
}

void QmuxConnection::push_event(QmuxEvent event) {
  events_.push_back(std::move(event));
}

}  // namespace webtransport::qmux

namespace {

using webtransport::qmux::QmuxConfig;
using webtransport::qmux::QmuxConnection;
using webtransport::qmux::QmuxEvent;
using webtransport::qmux::QmuxEventType;

nb::bytes event_data(const std::vector<uint8_t>& data) {
  return nb::bytes(reinterpret_cast<const char*>(data.data()), data.size());
}

}  // namespace

void bind_qmux(nb::module_& m) {
  nb::module_ qmux_m = m.def_submodule("qmux", "QMux (dwnx) の Sans-IO API");

  nb::class_<QmuxConfig>(qmux_m, "Config", "QMux 接続の設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("initial_max_streams_bidi", &QmuxConfig::initial_max_streams_bidi,
              "受け入れる同時双方向ストリーム数")
      .def_rw("initial_max_streams_uni", &QmuxConfig::initial_max_streams_uni,
              "受け入れる同時単方向ストリーム数")
      .def_rw("initial_max_data", &QmuxConfig::initial_max_data,
              "コネクション全体のフロー制御上限 (バイト)")
      .def_rw("initial_max_stream_data_bidi_local",
              &QmuxConfig::initial_max_stream_data_bidi_local,
              "双方向ストリーム (ローカル開始) のフロー制御上限 (バイト)")
      .def_rw("initial_max_stream_data_bidi_remote",
              &QmuxConfig::initial_max_stream_data_bidi_remote,
              "双方向ストリーム (リモート開始) のフロー制御上限 (バイト)")
      .def_rw("initial_max_stream_data_uni",
              &QmuxConfig::initial_max_stream_data_uni,
              "単方向ストリームのフロー制御上限 (バイト)")
      .def_rw("max_idle_timeout_ns", &QmuxConfig::max_idle_timeout_ns,
              "アイドルタイムアウト (ナノ秒)")
      .def_rw("max_record_size", &QmuxConfig::max_record_size,
              "1 レコードの最大長 (バイト)");

  nb::enum_<QmuxEventType>(qmux_m, "EventType", "QMux イベント種別")
      .value("TRANSPORT_PARAMS_RECEIVED",
             QmuxEventType::TransportParamsReceived)
      .value("STREAM_DATA", QmuxEventType::StreamData)
      .value("STREAM_CLOSED", QmuxEventType::StreamClosed)
      .value("STREAM_RESET", QmuxEventType::StreamReset)
      .value("STOP_SENDING", QmuxEventType::StopSending);

  nb::class_<QmuxEvent>(qmux_m, "Event", "QMux イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &QmuxEvent::type, "イベント種別")
      .def_ro("stream_id", &QmuxEvent::stream_id, "ストリーム ID")
      .def_ro("offset", &QmuxEvent::offset, "ストリーム上のオフセット")
      .def_prop_ro(
          "data", [](const QmuxEvent& event) { return event_data(event.data); },
          nb::sig("def data(self) -> bytes"), "ストリームデータ")
      .def_ro("fin", &QmuxEvent::fin, "FIN フラグ")
      .def_ro("error_code", &QmuxEvent::error_code, "エラーコード");

  nb::class_<QmuxConnection>(qmux_m, "Connection",
                             "QMux コネクション (Sans-IO)")
      .def_static(
          "create_client",
          [](const QmuxConfig& config) -> QmuxConnection* {
            return QmuxConnection::create_client(config).release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          "クライアントとして接続を作成")
      .def_static(
          "create_server",
          [](const QmuxConfig& config) -> QmuxConnection* {
            return QmuxConnection::create_server(config).release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          "サーバーとして接続を作成")
      .def(
          "receive",
          [](QmuxConnection& self, nb::bytes data) {
            return self.receive(reinterpret_cast<const uint8_t*>(data.c_str()),
                                data.size());
          },
          nb::arg("data"),
          "受信したバイト列を処理する (負値はライブラリエラー)")
      .def_prop_ro(
          "pending_record",
          [](QmuxConnection& self) -> nb::object {
            const auto record = self.send();
            if (!record) {
              return nb::none();
            }
            return event_data(*record);
          },
          nb::sig("def pending_record(self) -> bytes | None"),
          "送信すべき 1 レコード (無い場合は None)")
      .def_prop_ro(
          "timeout",
          [](const QmuxConnection& self) -> nb::object {
            const auto timeout = self.get_timeout();
            if (!timeout) {
              return nb::none();
            }
            return nb::int_(*timeout);
          },
          nb::sig("def timeout(self) -> int | None"),
          "次のタイマー期限 (ナノ秒)")
      .def("handle_timeout", &QmuxConnection::handle_timeout,
           "タイマーを処理する")
      .def("open_stream", &QmuxConnection::open_stream,
           nb::arg("bidirectional") = true, "ストリームを開く (失敗時は -1)")
      .def(
          "send_stream_data",
          [](QmuxConnection& self, int64_t stream_id, nb::bytes data,
             bool fin) {
            self.send_stream_data(
                stream_id, reinterpret_cast<const uint8_t*>(data.c_str()),
                data.size(), fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          "ストリームデータの送信を予約する")
      .def("close", &QmuxConnection::close, nb::arg("error_code") = 0,
           nb::arg("reason") = "",
           "接続を閉じる (CONNECTION_CLOSE のレコードを取り出せるようにする)")
      .def(
          "next_event",
          [](QmuxConnection& self) -> nb::object {
            auto event = self.next_event();
            if (!event) {
              return nb::none();
            }
            return nb::cast(std::move(*event));
          },
          nb::sig("def next_event(self) -> Event | None"),
          "次のイベントを取得する")
      .def_prop_ro("is_server", &QmuxConnection::is_server,
                   "サーバー接続かどうか")
      .def_prop_ro("streams_bidi_left", &QmuxConnection::streams_bidi_left,
                   "開設可能な残り双方向ストリーム数")
      .def_prop_ro("streams_uni_left", &QmuxConnection::streams_uni_left,
                   "開設可能な残り単方向ストリーム数")
      .def_static("strerror", &QmuxConnection::strerror, nb::arg("liberr"),
                  "ライブラリエラーコードを文字列にする");

  qmux_m.def(
      "get_version", []() { return std::string(DWNX_VERSION); },
      "dwnx のバージョン");
}
