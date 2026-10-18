#include "bindings/qmux.h"

#include <chrono>
#include <cstring>
#include <random>

#include "python_input.h"
#include "quic_varint.h"

#include <dwnx/version.h>
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
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

/**
 * CONNECTION_CLOSE の理由に使える最大バイト数を返す
 *
 * dwnx はレコードの Frames フィールドを DWNX_DEFAULT_MAX_RECORD_SIZE (16382)
 * バイトに制限する (dwnx_qre_start がレコード長を
 * DWNX_QRE_RECORDLEN_SIZE + DWNX_DEFAULT_MAX_RECORD_SIZE に切り詰め、
 * dwnx_qre_encode_frame が収まらないフレームに DWNX_ERR_NOBUF を返す)。
 * アプリケーション側からこの上限を引き上げる手段は無く
 * (dwnx_conn.c の "We do not let application increase max record size.")、
 * ピアのトランスポートパラメータも DWNX_DEFAULT_MAX_RECORD_SIZE 以上で
 * なければならないため、上限は接続の状態によらず一定である。
 *
 * CONNECTION_CLOSE (0x1d) のフレームは種別 (1 バイト)・終了コード (varint)・
 * 理由の長さ (varint)・理由から成るため (RFC 9000 Section 19.19)、理由に
 * 使えるのは残りである。理由の長さの varint 長は長さに依存するため、varint を
 * 差し引いてから求め直す (この範囲では 2 バイトで収束する)。
 */
size_t connection_close_reason_limit(uint64_t error_code) {
  size_t limit = DWNX_DEFAULT_MAX_RECORD_SIZE - 1 -
                 bindings::quic_varint_length(error_code);
  limit -= bindings::quic_varint_length(limit);
  return limit;
}

/**
 * 文字列を UTF-8 の文字境界で切り詰める
 *
 * 末尾の文字が途中で切れている場合は、その文字の先頭まで戻す。末尾が
 * 継続バイト (0b10xxxxxx) で終わる場合だけでなく、リードバイト
 * (0b11xxxxxx) で終わって後続バイトが欠けている場合も切れているため、
 * リードバイトから数えた文字の長さと実際に残っている長さを比べる。
 * RFC 9000 Section 19.19 は理由が UTF-8 であることを推奨しており、壊れた
 * 文字を送らないためである。
 */
void truncate_utf8(std::string& value, size_t limit) {
  if (value.size() <= limit) {
    return;
  }
  value.resize(limit);

  // UTF-8 の文字は最大 4 バイトなので、末尾から 3 バイト遡ればリードバイトが
  // 見付かる。見付からなければ UTF-8 として不正なため、そのままにする
  const size_t cursor = value.size();
  const size_t lower_bound = cursor > 3 ? cursor - 3 : 0;
  size_t head = cursor;
  while (head > lower_bound &&
         (static_cast<unsigned char>(value[head - 1]) & 0xc0) == 0x80) {
    --head;
  }
  if (head == 0) {
    return;
  }

  // リードバイトから期待される文字の長さを求める
  const unsigned char lead = static_cast<unsigned char>(value[head - 1]);
  size_t expected = 0;
  if ((lead & 0x80) == 0x00) {
    expected = 1;
  } else if ((lead & 0xe0) == 0xc0) {
    expected = 2;
  } else if ((lead & 0xf0) == 0xe0) {
    expected = 3;
  } else if ((lead & 0xf8) == 0xf0) {
    expected = 4;
  }

  if (expected != 0 && head - 1 + expected > cursor) {
    // 末尾の文字が途中で切れている
    value.resize(head - 1);
  }
}

/**
 * QUIC varint の上限を超えていれば std::invalid_argument を送出する
 *
 * nanobind の既定翻訳により Python 側では ValueError になる。
 */
void check_varint_value(uint64_t value, const char* name) {
  if (value > DWNX_MAX_VARINT) {
    throw std::invalid_argument(std::string(name) + " must be at most " +
                                std::to_string(DWNX_MAX_VARINT) + ": got " +
                                std::to_string(value));
  }
}

/**
 * 広告できるストリーム数の上限
 *
 * RFC 9000 Section 4.6 は max_streams のトランスポートパラメータと MAX_STREAMS
 * フレームを 2^60 以下と定める (2^60 を超えるとストリーム ID を可変長整数で
 * 表せなくなるため。パラメータの定義は Section 18.2、フレームの定義は
 * Section 19.11)。QMux もこれらのトランスポートパラメータの定義をそのまま
 * 使う (draft-ietf-quic-qmux-02 Section 5.1)。dwnx はデコーダで DWNX_MAX_STREAMS
 * (1<<60) を超える値のピアを MALFORMED_TRANSPORT_PARAM として拒否するため、
 * 超える値を広告すると接続が確立できない。
 */
constexpr uint64_t kMaxInitialStreams = 1ULL << 60;

/**
 * 1 レコードを書くための送信バッファの大きさ (バイト)
 *
 * dwnx は 1 レコードをレコード長 (2 バイト) + DWNX_DEFAULT_MAX_RECORD_SIZE に
 * 制限し、アプリケーションから引き上げさせないため、設定値によらずこの
 * 大きさで足りる。`max_record_size` をそのまま確保に使うと、大きな値を指定した
 * 利用者がメモリを使い果たす。
 */
constexpr size_t kWriteBufferSize = DWNX_DEFAULT_MAX_RECORD_SIZE + 4096;

/**
 * DATAGRAM フレームの全長 (バイト) を返す
 *
 * dwnx は DATAGRAM フレームを長さ付き (DWNX_FRAME_DATAGRAM_LEN) で符号化し、
 * 全長は型 (1 バイト) + 長さの varint + ペイロードになる
 * (dwnx_frame.c の dwnx_frame_datagram_framelen)。RFC 9221 Section 3 の
 * `max_datagram_frame_size` はフレーム全長との比較であるため、送信側の
 * 事前検査でも同じ計算を使う。
 */
size_t datagram_frame_length(size_t datalen) {
  return 1 + bindings::quic_varint_length(datalen) + datalen;
}

/**
 * QMux の設定値を検査する
 *
 * dwnx は conn_new とトランスポートパラメータの符号化で設定値を assert で
 * 検査する。同梱の libdwnx は NDEBUG なしでビルドされているため、範囲外の値は
 * Python の例外ではなくプロセスの abort (SIGABRT) になる。Python から渡された
 * 値でプロセスを落とさないよう、接続を作る前にここで弾く。検査する条件は
 * dwnx の assert と同じである (dwnx_conn.c / dwnx_conv.c)。
 *
 * Raises:
 *     std::invalid_argument: QUIC varint の上限 (2^62-1) を超える値がある場合、
 *         ストリーム数が 2^60 を超える場合、`max_record_size` が dwnx の下限
 *         (16382) 未満の場合、または `max_idle_timeout_ns` が UINT64_MAX の場合
 */
void validate_qmux_config(const QmuxConfig& config) {
  check_varint_value(config.initial_max_streams_bidi,
                     "QMux config initial_max_streams_bidi");
  check_varint_value(config.initial_max_streams_uni,
                     "QMux config initial_max_streams_uni");
  check_varint_value(config.initial_max_data, "QMux config initial_max_data");
  check_varint_value(config.initial_max_stream_data_bidi_local,
                     "QMux config initial_max_stream_data_bidi_local");
  check_varint_value(config.initial_max_stream_data_bidi_remote,
                     "QMux config initial_max_stream_data_bidi_remote");
  check_varint_value(config.initial_max_stream_data_uni,
                     "QMux config initial_max_stream_data_uni");
  check_varint_value(config.max_record_size, "QMux config max_record_size");
  // dwnx はトランスポートパラメータの符号化で varint の上限を assert で検査する
  check_varint_value(config.max_datagram_frame_size,
                     "QMux config max_datagram_frame_size");
  // ストリーム数の上限は varint の上限より小さい
  if (config.initial_max_streams_bidi > kMaxInitialStreams) {
    throw std::invalid_argument(
        "QMux config initial_max_streams_bidi must be at most " +
        std::to_string(kMaxInitialStreams) + ": got " +
        std::to_string(config.initial_max_streams_bidi));
  }
  if (config.initial_max_streams_uni > kMaxInitialStreams) {
    throw std::invalid_argument(
        "QMux config initial_max_streams_uni must be at most " +
        std::to_string(kMaxInitialStreams) + ": got " +
        std::to_string(config.initial_max_streams_uni));
  }
  // dwnx はアプリケーションからレコード長の上限を引き上げさせず、下限も
  // DWNX_DEFAULT_MAX_RECORD_SIZE で固定している
  if (config.max_record_size < DWNX_DEFAULT_MAX_RECORD_SIZE) {
    throw std::invalid_argument(
        "QMux config max_record_size must be at least " +
        std::to_string(DWNX_DEFAULT_MAX_RECORD_SIZE) + ": got " +
        std::to_string(config.max_record_size));
  }
  // UINT64_MAX は dwnx が「アイドルタイムアウト無し」の内部表現に使うため、
  // アプリケーションからは指定できない
  if (config.max_idle_timeout_ns == UINT64_MAX) {
    throw std::invalid_argument("QMux config max_idle_timeout_ns must not be " +
                                std::to_string(UINT64_MAX));
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
  auto* self = static_cast<QmuxConnection*>(user_data);
  // DATAGRAM の送信可否と上限はピアの広告で決まる (RFC 9221 Section 3)
  self->remote_transport_params_received_ = true;
  self->remote_max_datagram_frame_size_ = params->max_datagram_frame_size;
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

/**
 * DATAGRAM を受信した (全体または一部)
 */
int recv_datagram_cb(dwnx_conn* conn,
                     int fin,
                     size_t dgramlen,
                     size_t offset,
                     const uint8_t* data,
                     size_t datalen,
                     void* user_data) noexcept {
  (void)conn;
  auto* self = static_cast<QmuxConnection*>(user_data);
  self->on_datagram_received(fin, dgramlen, offset, data, datalen);
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
  // 範囲外の設定値で dwnx の assert (プロセスの abort) に到達する前に弾く
  validate_qmux_config(config);

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
  params.max_datagram_frame_size = config.max_datagram_frame_size;
  max_datagram_frame_size_ = config.max_datagram_frame_size;

  dwnx_callbacks callbacks{};
  callbacks.rand = rand_cb;
  callbacks.recv_transport_params = recv_transport_params_cb;
  callbacks.recv_stream_data = recv_stream_data_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.stream_reset = stream_reset_cb;
  callbacks.recv_stop_sending = recv_stop_sending_cb;
  callbacks.recv_datagram = recv_datagram_cb;

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

  write_buffer_.resize(kWriteBufferSize);
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

std::optional<std::vector<uint8_t>> QmuxConnection::write_pending_datagrams() {
  // dwnx の契約: WRITE_MORE の間は writev_stream / writev_datagram /
  // write_record のいずれかを呼び続ける。ここでは DATAGRAM だけを書き、
  // 続きが無くなったら send() の write_record が record を確定する
  for (int attempt = 0; attempt < 64 && !pending_datagrams_.empty();
       ++attempt) {
    auto& data = pending_datagrams_.front();
    dwnx_vec vec{};
    vec.base = data.data();
    vec.len = data.size();
    int accepted = 0;
    const dwnx_ssize n = dwnx_conn_writev_datagram(
        conn_, write_buffer_.data(), write_buffer_.size(), &accepted,
        DWNX_WRITE_DATAGRAM_FLAG_NONE, &vec, 1, now_ns());

    if (n > 0) {
      // 1 レコード分が完成した。DATAGRAM が載らなかった場合
      // (accepted == 0) は、送信待ちに残して次の record へ回す
      if (accepted != 0) {
        pending_datagrams_.pop_front();
      }
      return std::vector<uint8_t>(write_buffer_.begin(),
                                  write_buffer_.begin() + n);
    }

    if (n == DWNX_ERR_WRITE_MORE) {
      // 同じレコードにまだ詰められる。載った分だけ消費して続ける
      if (accepted != 0) {
        pending_datagrams_.pop_front();
        continue;
      }
      // 進捗が無い場合は次の送信機会に回す
      break;
    }

    if (n == DWNX_ERR_INVALID_ARGUMENT) {
      // ピアの max_datagram_frame_size をフレーム長が超えている
      // (RFC 9221 Section 3 の MUST NOT)。送信待ちの先頭を破棄して
      // 後続を詰まらせない
      pending_datagrams_.pop_front();
      continue;
    }

    if (n == DWNX_ERR_INVALID_STATE) {
      if (remote_transport_params_received_) {
        // ピアが DATAGRAM を広告していない。接続中は解消しないため破棄する
        pending_datagrams_.pop_front();
        continue;
      }
      // ピアのトランスポートパラメータを受信するまでは上限が不明である。
      // 破棄せずに残し、次の送信機会に回す
      break;
    }

    // 0 (書けるものが無い) とその他のエラー
    break;
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

void QmuxConnection::on_datagram_received(int fin,
                                          size_t dgramlen,
                                          size_t offset,
                                          const uint8_t* data,
                                          size_t datalen) {
  // offset == 0 はデータグラムの先頭である。前回のデータグラムが
  // dgramlen と一致せず破棄された場合に備えて、ここでバッファを初期化する
  if (offset == 0) {
    datagram_buffer_.clear();
  }
  if (datalen != 0) {
    datagram_buffer_.insert(datagram_buffer_.end(), data, data + datalen);
  }
  if (fin == 0) {
    return;
  }

  // 組み立てた長さが dwnx の通知した全長と一致しない場合は、受信が
  // 不連続だったとみなして破棄する。dwnx は record を順に処理するため
  // 通常は起こらないが、壊れたデータグラムをアプリへ渡さない
  if (datagram_buffer_.size() != dgramlen) {
    datagram_buffer_.clear();
    return;
  }

  QmuxEvent event;
  event.type = QmuxEventType::DatagramReceived;
  event.data = std::move(datagram_buffer_);
  datagram_buffer_.clear();
  push_event(std::move(event));
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
  // ストリームデータを書き終えた record は上で確定しているため、DATAGRAM は
  // 続く record へ書く。write_pending_datagrams が WRITE_MORE で record を
  // 開始したまま戻った場合は、下の write_record がそれを確定する
  // (dwnx の WRITE_MORE の契約は writev_stream / writev_datagram /
  // write_record のいずれかを呼び続けることである)。
  if (auto record = write_pending_datagrams()) {
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

void QmuxConnection::send_datagram(const std::vector<uint8_t>& data) {
  if (conn_ == nullptr || close_requested_) {
    return;
  }
  // ローカルが DATAGRAM を広告していない場合は送信しない。QUIC 層の
  // `send_datagram` も `enable_datagram` が無効なら何もしない
  if (max_datagram_frame_size_ == 0) {
    return;
  }
  const size_t frame_length = datagram_frame_length(data.size());
  // QMux の DATAGRAM フレームは record を跨げない
  // (draft-ietf-quic-qmux-02 Section 3.2 / Section 9.1)。record の Frames
  // フィールドに収まらないデータグラムは送信できないため破棄する。
  // dwnx は record の Frames フィールドを DWNX_DEFAULT_MAX_RECORD_SIZE に
  // 固定しており、ピアの max_record_size もその値以上でなければならない
  // (dwnx_conn.c の conn_recv_transport_params) ため、上限は接続の状態に
  // よらず一定である
  if (frame_length > DWNX_DEFAULT_MAX_RECORD_SIZE) {
    return;
  }
  // ピアの上限が判明していれば、フレーム長がそれを超えるデータグラムは
  // 送出しない (RFC 9221 Section 3 の MUST NOT)
  if (remote_transport_params_received_ &&
      frame_length > remote_max_datagram_frame_size_) {
    return;
  }
  pending_datagrams_.emplace_back(data);
}

int QmuxConnection::reset_stream(int64_t stream_id, uint64_t error_code) {
  // dwnx は終了コードを varint として符号化する際に assert で上限を検査する
  check_varint_value(error_code, "QMux error_code");
  if (conn_ == nullptr) {
    return DWNX_ERR_INVALID_ARGUMENT;
  }
  // 予約された RESET_STREAM は後続の writev_stream / write_record が送出する
  return dwnx_conn_shutdown_stream_write(conn_, 0, stream_id, error_code);
}

int QmuxConnection::stop_sending(int64_t stream_id, uint64_t error_code) {
  check_varint_value(error_code, "QMux error_code");
  if (conn_ == nullptr) {
    return DWNX_ERR_INVALID_ARGUMENT;
  }
  // 予約された STOP_SENDING は後続の writev_stream / write_record が送出する
  return dwnx_conn_shutdown_stream_read(conn_, 0, stream_id, error_code);
}

int QmuxConnection::close_stream(int64_t stream_id, uint64_t error_code) {
  check_varint_value(error_code, "QMux error_code");
  if (conn_ == nullptr) {
    return DWNX_ERR_INVALID_ARGUMENT;
  }
  // 単方向ストリームでは dwnx が存在する側だけを終了する
  return dwnx_conn_shutdown_stream(conn_, 0, stream_id, error_code);
}

void QmuxConnection::close(uint64_t error_code, const std::string& reason) {
  check_varint_value(error_code, "QMux error_code");
  if (conn_ == nullptr) {
    return;
  }
  // dwnx_ccerr_set_application_error は reason をコピーしない (dwnx.h に
  // 「This function does not make a copy of the reason phrase」と明記されて
  // いる)。CONNECTION_CLOSE のレコードを書くのは後続の send()
  // (write_connection_close) であるため、reason の実体をメンバに保持して
  // 書き終わるまで生存させる。引数の reason は nanobind が Python の str から
  // 作る一時オブジェクトで、close() を抜けた時点で破棄される
  close_reason_ = reason;
  // 理由が 1 レコードに収まらないと CONNECTION_CLOSE を 1 つも書けず、終了
  // コードもピアへ届かない。理由の長さによって終了コードが失われないよう、
  // 収まる長さへ UTF-8 の文字境界で切り詰める
  truncate_utf8(close_reason_, connection_close_reason_limit(error_code));
  dwnx_ccerr_default(&close_ccerr_);
  dwnx_ccerr_set_application_error(
      &close_ccerr_, error_code,
      reinterpret_cast<const uint8_t*>(close_reason_.data()),
      close_reason_.size());
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

std::optional<uint64_t> QmuxConnection::remote_max_datagram_frame_size() const {
  if (!remote_transport_params_received_) {
    return std::nullopt;
  }
  return remote_max_datagram_frame_size_;
}

uint64_t QmuxConnection::local_max_datagram_frame_size() const {
  return max_datagram_frame_size_;
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
              "受け入れる同時双方向ストリーム数。2^60 以下でなければならない")
      .def_rw("initial_max_streams_uni", &QmuxConfig::initial_max_streams_uni,
              "受け入れる同時単方向ストリーム数。2^60 以下でなければならない")
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
              "1 レコードの最大長 (バイト)。16382 以上でなければならない。"
              "dwnx は送信のレコード長を 16382 に固定するため、それより大きい"
              "値は記録長に影響しない")
      .def_rw("max_datagram_frame_size", &QmuxConfig::max_datagram_frame_size,
              "受信できる DATAGRAM フレームの最大長 (バイト)。0 なら DATAGRAM "
              "を広告せず、send_datagram も無効になる。既定値は 16382 "
              "(draft-ietf-quic-qmux-02 Section 9.1)");

  nb::enum_<QmuxEventType>(qmux_m, "EventType", "QMux イベント種別")
      .value("TRANSPORT_PARAMS_RECEIVED",
             QmuxEventType::TransportParamsReceived)
      .value("STREAM_DATA", QmuxEventType::StreamData)
      .value("STREAM_CLOSED", QmuxEventType::StreamClosed)
      .value("STREAM_RESET", QmuxEventType::StreamReset)
      .value("STOP_SENDING", QmuxEventType::StopSending)
      .value("DATAGRAM", QmuxEventType::DatagramReceived);

  nb::class_<QmuxEvent>(qmux_m, "Event", "QMux イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &QmuxEvent::type, "イベント種別")
      .def_ro("stream_id", &QmuxEvent::stream_id, "ストリーム ID")
      .def_ro("offset", &QmuxEvent::offset, "ストリーム上のオフセット")
      .def_prop_ro(
          "data", [](const QmuxEvent& event) { return event_data(event.data); },
          nb::sig("def data(self) -> bytes"),
          "イベントのデータ (STREAM_DATA はストリームデータ、DATAGRAM は "
          "DATAGRAM のペイロード全体)")
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
      .def(
          "send_datagram",
          [](QmuxConnection& self, nb::bytes data) {
            webtransport::bindings::check_python_input_size(
                "send_datagram data", data.size());
            self.send_datagram(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"),
          nb::sig("def send_datagram(self, data: bytes) -> None"),
          "DATAGRAM の送信を予約する (ローカルまたはピアが DATAGRAM を"
          "広告していない場合、record に収まらない場合、ピアの上限を超える"
          "場合は黙って破棄する。data が 1 MiB 超の場合は ValueError)")
      .def("reset_stream", &QmuxConnection::reset_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> int"),
           "ストリームの書き込み側をリセットする (RESET_STREAM を送出。"
           "0 以上は成功、負値は dwnx のライブラリエラー。終了コードが QUIC "
           "varint の上限 (2^62-1) を超える場合は ValueError)")
      .def("stop_sending", &QmuxConnection::stop_sending, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def stop_sending(self, stream_id: int, error_code: int = "
                   "0) -> int"),
           "ストリームの読み取り側を終了する (STOP_SENDING を送出。"
           "0 以上は成功、負値は dwnx のライブラリエラー。終了コードが QUIC "
           "varint の上限 (2^62-1) を超える場合は ValueError)")
      .def("close_stream", &QmuxConnection::close_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def close_stream(self, stream_id: int, error_code: int = "
                   "0) -> int"),
           "ストリームの両方向を終了する (RESET_STREAM + STOP_SENDING。"
           "0 以上は成功、負値は dwnx のライブラリエラー。終了コードが QUIC "
           "varint の上限 (2^62-1) を超える場合は ValueError)")
      .def(
          "close", &QmuxConnection::close, nb::arg("error_code") = 0,
          nb::arg("reason") = "",
          "接続を閉じる (CONNECTION_CLOSE のレコードを取り出せるようにする。"
          "理由が 1 レコードに収まらない場合は UTF-8 の文字境界で切り詰める。"
          "終了コードが QUIC varint の上限 (2^62-1) を超える場合は ValueError)")
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
      .def("remote_max_datagram_frame_size",
           &QmuxConnection::remote_max_datagram_frame_size,
           nb::sig("def remote_max_datagram_frame_size(self) -> int | None"),
           "ピアの DATAGRAM フレームサイズ上限 (未受信なら None)")
      .def_prop_ro("local_max_datagram_frame_size",
                   &QmuxConnection::local_max_datagram_frame_size,
                   "ローカルの DATAGRAM フレームサイズ上限 (0 は無効)")
      .def_static("strerror", &QmuxConnection::strerror, nb::arg("liberr"),
                  "ライブラリエラーコードを文字列にする");

  qmux_m.def(
      "get_version", []() { return std::string(DWNX_VERSION); },
      "dwnx のバージョン");
}
