/**
 * WebTransport over HTTP/2 バインディング実装 (draft-ietf-webtrans-http2-15)
 *
 * Capsule Protocol (RFC 9297) を使用した実装
 */

#include "webtransport_h2.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <stdexcept>
#include <string>

namespace webtransport {
namespace h2 {

namespace {

// 対向から受信した上限を記録する。未受信ならそのまま格納し、受信済みなら
// 大きい方を残す (SETTINGS と WebTransport-Init の大きい方を採用する)
void record_received_limit(std::optional<uint64_t>& received, uint64_t value) {
  if (!received.has_value() || value > *received) {
    received = value;
  }
}

// draft-15 Section 6.7 / 6.10: Maximum Streams は 2^60 を超えてはならない
constexpr uint64_t kMaxStreamsLimit = 1ULL << 60;

// 0x50 は WT_FLOW_CONTROL_ERROR (draft-15 Section 3.4 の 0xTBD) の
// プレースホルダ。draft で値が確定したら更新する
constexpr uint32_t kWtFlowControlError = 0x50;

// 0x52 は WT_ERROR (draft-15 Section 3.4 の 0xTBD) のプレースホルダ。
// draft で値が確定したら更新する
constexpr uint32_t kWtError = 0x52;

// draft-15 Section 6.12 の Application Error Message 上限 (バイト)
constexpr size_t kMaxApplicationErrorMessageBytes = 1024;

// draft-15 Section 6.12 の "valid UTF-8" を RFC 3629 の well-formed UTF-8
// として検査する。
// overlong 符号化、サロゲート (U+D800..U+DFFF)、 U+10FFFF 超、
// 不完全シーケンス、非先頭バイトを拒否する
bool is_valid_utf8(const uint8_t* data, size_t length) {
  size_t offset = 0;
  while (offset < length) {
    if (data[offset] <= 0x7F) {
      offset += 1;
      continue;
    }
    size_t extra = 0;
    uint32_t min_codepoint = 0;
    uint32_t codepoint = 0;
    if ((data[offset] & 0xE0) == 0xC0) {
      extra = 1;
      min_codepoint = 0x80;
      codepoint = data[offset] & 0x1F;
    } else if ((data[offset] & 0xF0) == 0xE0) {
      extra = 2;
      min_codepoint = 0x800;
      codepoint = data[offset] & 0x0F;
    } else if ((data[offset] & 0xF8) == 0xF0) {
      extra = 3;
      min_codepoint = 0x10000;
      codepoint = data[offset] & 0x07;
    } else {
      return false;
    }
    if (offset + 1 + extra > length) {
      return false;
    }
    for (size_t index = 1; index <= extra; ++index) {
      if ((data[offset + index] & 0xC0) != 0x80) {
        return false;
      }
      codepoint = (codepoint << 6) | (data[offset + index] & 0x3F);
    }
    if (codepoint < min_codepoint || codepoint > 0x10FFFF ||
        (codepoint >= 0xD800 && codepoint <= 0xDFFF)) {
      return false;
    }
    offset += 1 + extra;
  }
  return true;
}

}  // namespace

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
  while (true) {
    wt_session = get_wt_session(session_id);
    if (!wt_session) {
      break;
    }
    // 受信ハンドラ内で close_session が呼ばれた場合 (WT_STREAM_STATE_ERROR
    // 等のエラー検知) は is_terminated が立つため、同一 receive() 内の
    // 後続カプセルを処理しない (終了済みセッションへの処理を防ぐ。
    // WT_CLOSE_SESSION の二重キュー自体は close_session 冒頭のガードで
    // 防がれる)。バッファに残った後続カプセルも破棄する
    if (wt_session->is_terminated) {
      wt_session->capsule_buffer.clear();
      break;
    }
    if (wt_session->capsule_buffer.empty()) {
      break;
    }

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

    // ペイロードをコピーしてから処理する
    // (ハンドラがセッションを削除しても安全にするため)
    std::vector<uint8_t> payload_copy(buf + header_len,
                                      buf + header_len + payload_len);
    CapsuleType capsule_type = static_cast<CapsuleType>(type_value);

    wt_session->capsule_buffer.erase(
        wt_session->capsule_buffer.begin(),
        wt_session->capsule_buffer.begin() +
            static_cast<std::ptrdiff_t>(header_len + payload_len));

    process_capsule(session_id, capsule_type, payload_copy.data(),
                    payload_copy.size());
  }
}

void H2Session::process_capsule(int32_t session_id,
                                CapsuleType type,
                                const uint8_t* payload,
                                size_t length) {
  switch (type) {
    case CapsuleType::WtStream:
    case CapsuleType::WtStreamFin: {
      // draft-15 Section 6.4: Type の最下位ビットが FIN
      bool fin = (static_cast<uint64_t>(type) & 0x01ULL) != 0;
      handle_wt_stream(session_id, fin, payload, length);
      break;
    }
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
    case CapsuleType::WtStreamsBlockedBidi:
    case CapsuleType::WtStreamsBlockedUni:
      handle_wt_streams_blocked(session_id, payload, length);
      break;
    case CapsuleType::Padding:
    case CapsuleType::WtDataBlocked:
    case CapsuleType::WtStreamDataBlocked:
      // フロー制御通知・PADDING は現時点では状態更新のみ不要
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

  // ストリームが存在しない場合は作成 (draft-15 Section 6.4 の暗黙作成)
  auto stream_it = wt_session->streams.find(stream_id);
  if (stream_it == wt_session->streams.end()) {
    WtStreamInfo info;
    info.stream_id = stream_id;
    info.is_local = false;
    info.is_unidirectional = (stream_id & 0x02) != 0;
    initialize_stream_send_credit(*wt_session, info);
    info.max_stream_data_remote = config_.wt_initial_max_stream_data;
    stream_it = wt_session->streams.emplace(stream_id, std::move(info)).first;
  }

  auto& stream_info = stream_it->second;

  // データ部分
  const uint8_t* stream_data = payload + stream_id_len;
  size_t data_len = length - stream_id_len;

  // 受信側終端状態 (DataRecvd / ResetRecvd) のストリームへのデータ付き
  // WT_STREAM 受信は stream error (draft-15 Section 6.4 の「A WT_STREAM
  // capsule MUST NOT be sent after a stream is closed or reset... A stream
  // error (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent」)。
  // データを含まない WT_STREAM (FIN のみ) は「ストリームを閉じる」操作として
  // 許容される (Section 6.4 の「Empty WT_STREAM capsules MUST NOT be used
  // unless they open or close a stream」)。実ブラウザ (WebKit) は FIN 送信後
  // に空の WT_STREAM_FIN を送ることがあるため、相互運用性の観点から無視する
  // (データ付きの受信は終端状態へのデータ送信を意味するため検知する)。
  // 状態検知はフロー制御チェックより前に置く (フロー制御違反の error code
  // 0x50 と区別するため)。エラー検知の実装は report_stream_state_error に
  // 集約する
  if (data_len > 0 &&
      (stream_info.recv_state == StreamState::DataRecvd ||
       stream_info.recv_state == StreamState::ResetRecvd)) {
    report_stream_state_error(session_id, stream_id,
                              "WT_STREAM received for stream in terminal state");
    return;
  }

  // draft-15 Section 6.5 / 6.6: 受信超過は WT_FLOW_CONTROL_ERROR で
  // セッションを閉じる。Error イベントの push と close_session は
  // report_recv_flow_control_error に集約する
  if (wt_session->bytes_received + data_len > wt_session->max_data_remote ||
      stream_info.bytes_received + data_len >
          stream_info.max_stream_data_remote) {
    report_recv_flow_control_error(session_id, stream_id,
                                   "peer exceeded flow control limit");
    return;
  }

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
  stream_info.bytes_received += data_len;
  wt_session->bytes_received += data_len;

  // FIN 受信で受信側を DataRecvd に遷移させる (draft-15 Section 5.2 の
  // QUIC 状態ミラー。以後の WT_STREAM / WT_RESET_STREAM 受信は冒頭の状態
  // 検証で stream error になる)
  if (fin) {
    stream_info.recv_state = StreamState::DataRecvd;
  }
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

  // Reliable Size
  auto reliable_size_result = decode_varint(payload + offset, length - offset);
  if (!reliable_size_result) {
    return;
  }
  auto [reliable_size, reliable_size_len] = *reliable_size_result;

  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  auto stream_it = wt_session->streams.find(stream_id);
  if (stream_it == wt_session->streams.end()) {
    // 真に未知のストリームへの WT_RESET_STREAM。受信済みバイト数は 0 として
    // Reliable Size と比較する (draft-15 Section 6.2 の MUST)。> 0 は
    // 届かないはずのデータを約束するため session error でセッションを閉じる。
    // = 0 は受け入れ、暗黙作成と同様の初期化でエントリを作成して受信側を
    // ResetRecvd へ遷移させる (以後の WT_STREAM は冒頭の状態検証で stream
    // error になる)。エラー検知時は StreamReset イベントを push しない
    if (reliable_size > 0) {
      report_stream_state_error(
          session_id, stream_id,
          "WT_RESET_STREAM non-zero reliable size, unknown stream");
      return;
    }
    WtStreamInfo info;
    info.stream_id = stream_id;
    info.is_local = false;
    info.is_unidirectional = (stream_id & 0x02) != 0;
    initialize_stream_send_credit(*wt_session, info);
    info.max_stream_data_remote = config_.wt_initial_max_stream_data;
    info.recv_state = StreamState::ResetRecvd;
    wt_session->streams[stream_id] = std::move(info);

    H2Event event;
    event.type = H2EventType::StreamReset;
    event.session_id = session_id;
    event.stream_id = stream_id;
    event.error_code = static_cast<uint32_t>(error_code);
    push_event(std::move(event));
    return;
  }

  auto& stream_info = stream_it->second;

  // 受信側終端状態 (DataRecvd / ResetRecvd) のストリームへの WT_RESET_STREAM
  // 受信は stream error (draft-15 Section 6.2 の「A WT_RESET_STREAM capsule
  // MUST NOT be sent after a stream is closed or reset... A stream error
  // (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent」)
  if (stream_info.recv_state == StreamState::DataRecvd ||
      stream_info.recv_state == StreamState::ResetRecvd) {
    report_stream_state_error(session_id, stream_id,
                              "WT_RESET_STREAM received for stream in terminal state");
    return;
  }

  // Reliable Size と受信済みバイト数の一致を検証する (draft-15 Section 6.2
  // の「A receiver MUST close the WebTransport session with a
  // WT_STREAM_STATE_ERROR session error if the Reliable Size in a
  // WT_RESET_STREAM capsule does not equal the number of bytes received on
  // that stream: ...」)。
  // 不一致時は StreamReset イベントを push せず session error でセッション
  // を閉じる
  if (reliable_size != stream_info.bytes_received) {
    report_stream_state_error(session_id, stream_id,
                              "WT_RESET_STREAM reliable size mismatch");
    return;
  }

  // 受信側を ResetRecvd に遷移させる (draft-15 Section 5.2 の QUIC 状態
  // ミラー。以後の WT_STREAM / WT_RESET_STREAM 受信は stream error になる)
  stream_info.recv_state = StreamState::ResetRecvd;

  H2Event event;
  event.type = H2EventType::StreamReset;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.error_code = static_cast<uint32_t>(error_code);
  push_event(std::move(event));
}

void H2Session::report_stream_state_error(int32_t session_id,
                                          uint64_t stream_id,
                                          const std::string& error_message) {
  // 0x51 は WT_STREAM_STATE_ERROR (draft-15 Section 3.4 の 0xTBD) の
  // プレースホルダ。draft で値が確定したら更新する
  constexpr uint32_t kWtStreamStateError = 0x51;
  // 検知した WT_STREAM_STATE_ERROR をアプリに通知してからセッションを閉じる。
  // close_session は送信をキューするのみで nghttp2_session_send を呼ばないため
  // mem_recv コールバック中でも安全であり、即座に is_terminated を立てて同一
  // receive() 内の後続カプセルを遮断する
  H2Event event;
  event.type = H2EventType::Error;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.error_code = kWtStreamStateError;
  event.error_message = error_message;
  push_event(std::move(event));
  close_session(session_id, kWtStreamStateError, error_message);
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

  auto* wt_session = get_wt_session(session_id);
  if (!wt_session) {
    return;
  }

  // draft-15 Section 6.3: 同一ストリームへの 2 回目の WT_STOP_SENDING は
  // WT_STREAM_STATE_ERROR
  if (wt_session->received_stop_sending_stream_ids.contains(stream_id)) {
    report_stream_state_error(session_id, stream_id,
                              "WT_STOP_SENDING received twice");
    return;
  }
  wt_session->received_stop_sending_stream_ids.insert(stream_id);

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
  if (!wt_session) {
    return;
  }

  // draft-15 Section 6.5: 前回受信値より小さい WT_MAX_DATA は
  // WT_FLOW_CONTROL_ERROR。比較対象は受信値のみ (フォールバックしない)
  if (wt_session->received_max_data.has_value() &&
      max_data < *wt_session->received_max_data) {
    report_flow_control_error(session_id, "WT_MAX_DATA decreased");
    return;
  }
  wt_session->received_max_data = max_data;
  if (max_data > wt_session->max_data_local) {
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
  if (!wt_session) {
    return;
  }

  auto stream_it = wt_session->streams.find(stream_id);
  std::optional<uint64_t> previous;
  auto capsule_it = wt_session->received_max_stream_data_by_id.find(stream_id);
  if (capsule_it != wt_session->received_max_stream_data_by_id.end()) {
    previous = capsule_it->second;
  } else {
    // カプセル未受信なら SETTINGS / WebTransport-Init の初期値が
    // 「前回受信値」 (draft-15 Section 6.6)
    // Bit 0: initiator (0 = client, 1 = server)
    // Bit 1: directionality (0 = bidi, 1 = uni)
    bool is_unidirectional = (stream_id & 0x02) != 0;
    bool is_local = ((stream_id & 0x01) != 0) == is_server_;
    previous = advertised_stream_send_credit(*wt_session, is_unidirectional,
                                             is_local);
  }

  if (previous.has_value() && max_data < *previous) {
    report_flow_control_error(session_id, "WT_MAX_STREAM_DATA decreased");
    return;
  }

  wt_session->received_max_stream_data_by_id[stream_id] = max_data;
  if (stream_it != wt_session->streams.end()) {
    if (max_data > stream_it->second.max_stream_data_local) {
      stream_it->second.max_stream_data_local = max_data;
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
  if (!wt_session) {
    return;
  }

  // draft-15 Section 6.7: Maximum Streams は 2^60 を超えてはならない
  if (max_streams > kMaxStreamsLimit) {
    report_flow_control_error(session_id, "WT_MAX_STREAMS exceeds 2^60");
    return;
  }

  auto& received = is_bidi ? wt_session->received_max_streams_bidi
                           : wt_session->received_max_streams_uni;
  auto& credit = is_bidi ? wt_session->max_streams_bidi_local
                         : wt_session->max_streams_uni_local;

  // 前回受信値より小さい WT_MAX_STREAMS は WT_FLOW_CONTROL_ERROR
  if (received.has_value() && max_streams < *received) {
    report_flow_control_error(session_id, "WT_MAX_STREAMS decreased");
    return;
  }
  received = max_streams;
  if (max_streams > credit) {
    credit = max_streams;
  }
}

void H2Session::handle_wt_streams_blocked(int32_t session_id,
                                          const uint8_t* payload,
                                          size_t length) {
  auto max_streams_result = decode_varint(payload, length);
  if (!max_streams_result) {
    return;
  }
  auto [max_streams, max_streams_len] = *max_streams_result;

  if (!get_wt_session(session_id)) {
    return;
  }

  // draft-15 Section 6.10: Maximum Streams が 2^60 を超える値は
  // WT_FLOW_CONTROL_ERROR。減少値の受信側 MUST は無く、 advisory な通知
  // のため状態は更新しない
  if (max_streams > kMaxStreamsLimit) {
    report_flow_control_error(session_id, "WT_STREAMS_BLOCKED exceeds 2^60");
    return;
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
  const uint8_t* message_bytes = nullptr;
  size_t message_len = 0;

  if (length >= 4) {
    error_code = (static_cast<uint32_t>(payload[0]) << 24) |
                 (static_cast<uint32_t>(payload[1]) << 16) |
                 (static_cast<uint32_t>(payload[2]) << 8) |
                 static_cast<uint32_t>(payload[3]);
    if (length > 4) {
      message_bytes = payload + 4;
      message_len = length - 4;
    }
  }

  // draft-15 Section 6.12: Application Error Message は 1024 バイト以下の
  // 正しい UTF-8 である。超過または不正な UTF-8 は WT_ERROR セッションエラー。
  // 受信した不正メッセージは close_session へ渡さない (再送出と、送信側の
  // 1024 切り詰めが文字境界を無視して新たな不正 UTF-8 を作るのを防ぐ)
  if (message_len > kMaxApplicationErrorMessageBytes) {
    report_wt_error(session_id, "WT_CLOSE_SESSION message exceeds 1024 bytes");
    return;
  }
  if (message_len > 0 && !is_valid_utf8(message_bytes, message_len)) {
    report_wt_error(session_id, "WT_CLOSE_SESSION message is not valid UTF-8");
    return;
  }

  std::string error_message;
  if (message_len > 0) {
    error_message.assign(reinterpret_cast<const char*>(message_bytes),
                         message_len);
  }

  H2Event event;
  event.type = H2EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = error_code;
  event.error_message = error_message;
  push_event(std::move(event));

  // エントリを削除して以後の on_stream_close_callback / close_session /
  // send_datagram / send_stream_data / open_stream / reset_stream /
  // stop_sending / drain_session をエントリ不在で塞ぐ (handle_end_stream
  // と同じ扱い)。キュー済みのカプセル (http2_stream_buffers_) も破棄する:
  // 終了を学習した後にキュー済みの送出を行わないため (handle_end_stream と
  // 対称)。破棄で失われるのは終了前にキュー済みの未 flush のカプセル (データ
  // グラム等) であり、応答の END_STREAM は以下で直接送出するため応答カプセル
  // は発生しない。draft-15 Section 6.12 の受信者 MUST (WT_CLOSE_SESSION
  // 受信時に END_STREAM フレームで応答してストリームを閉じる) に従い、
  // 応答の END_STREAM を送出する (end_stream_pending_ による half-close)。
  // エントリは削除済みのため、両ハーフクローズ時の on_stream_close_callback
  // は SessionClosed を発火しない (二重発火しない)。コンプライアントなピア
  // (Section 6.12 の MUST で END_STREAM を送る) なら応答の END_STREAM と
  // 合わせて両ハーフが閉じてクローズし、同時ストリーム枠を消費し続けない。
  // END_STREAM を送らないピアでは、自側は応答の END_STREAM で half-closed
  // (local) になったままピアの END_STREAM 待ちでストリームは閉じない
  // (handle_end_stream の経路は自側が END_STREAM を送らない点が異なる)
  http2_stream_buffers_.erase(session_id);
  wt_sessions_.erase(session_id);
  end_stream_pending_.insert(session_id);
  nghttp2_session_resume_data(session_, session_id);
}

void H2Session::handle_wt_drain_session(int32_t session_id) {
  H2Event event;
  event.type = H2EventType::SessionDraining;
  event.session_id = session_id;
  push_event(std::move(event));
}

void H2Session::handle_end_stream(int32_t session_id) {
  // ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送って CONNECT ストリーム
  // を閉じた場合のセッション終了処理 (draft-15 Section 3.4 の正規の終了経路)。
  // 対象は確立済み (is_established) のセッションに限定する: 非 2xx 拒否、
  // 201 応答、サーバー側の受理前 FIN は確立されておらず、誤検知しない
  // (非 2xx 拒否は応答受信時にエントリ削除済み)。WT_CLOSE_SESSION 受信済み
  // のセッションは handle_wt_close_session がエントリを削除済みのため、
  // get_wt_session が失敗してここで返る。ローカル close_session 済みの
  // セッションは is_terminated のためスキップする: コンプライアントなピアは
  // WT_CLOSE_SESSION 送出後に必ず END_STREAM を送る (Section 6.12 の MUST)
  // ため、カプセル処理による SessionClosed の後に検知が来て二重発火する。
  // handle_wt_close_session と並置する (共通ヘルパー化はしない): 並置時に
  // エントリ削除・バッファ破棄を欠落させると「エントリ不在で塞がる」前提が
  // 崩れる
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || !wt_session->is_established || wt_session->is_terminated) {
    return;
  }

  // WT_CLOSE_SESSION なしのクリーンクローズは error code 0 かつ空のエラー
  // 文字列の WT_CLOSE_SESSION と等価 (Section 6.12)
  H2Event event;
  event.type = H2EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = 0;
  push_event(std::move(event));

  // エントリを削除して以後の on_stream_close_callback / close_session /
  // send_datagram / send_stream_data / open_stream / reset_stream /
  // stop_sending / drain_session をエントリ不在で塞ぐ。キュー済みの
  // カプセル (http2_stream_buffers_) も破棄する: 200 + END_STREAM (受理と
  // 同時クローズ) では同一 receive() 内で確立処理が初期フロー制御カプセル
  // をキューしており、セッション終了を学習した後に送出しないため
  // (on_stream_close_callback のバッファ破棄と対称)。ピアの END_STREAM に
  // 対する自側の応答 (END_STREAM 送出) は行わない (ストリームは
  // half-closed (remote) のまま接続終了まで残る既知の制約。Section 6.12 の
  // 受信者側 MUST は WT_CLOSE_SESSION 受信時の応答についての規定であり、
  // END_STREAM のみの受信には該当しない)
  http2_stream_buffers_.erase(session_id);
  wt_sessions_.erase(session_id);
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

void H2Session::push_event(H2Event event) {
  events_.push_back(std::move(event));
}

WtSessionInfo* H2Session::get_wt_session(int32_t session_id) {
  auto it = wt_sessions_.find(session_id);
  if (it == wt_sessions_.end()) {
    return nullptr;
  }
  return &it->second;
}

void H2Session::apply_peer_initial_flow_control(WtSessionInfo& wt_session) const {
  // 対向 SETTINGS が我々の送信上限 (local)、自側 config が受信上限 (remote)
  // draft-15 Section 4.3.1
  // 対向が 0 のままの場合は capsule 到着まで送れないため、自側 config を下限にする
  // (SETTINGS 未受信や default 0 へのフォールバック)
  wt_session.max_data_local =
      peer_wt_initial_max_data_ > 0 ? peer_wt_initial_max_data_
                                    : config_.wt_initial_max_data;
  wt_session.max_data_remote = config_.wt_initial_max_data;
  if (peer_wt_initial_max_data_ > 0) {
    wt_session.received_max_data = peer_wt_initial_max_data_;
  }
  wt_session.max_streams_bidi_local =
      peer_wt_initial_max_streams_bidi_ > 0
          ? peer_wt_initial_max_streams_bidi_
          : config_.wt_initial_max_streams_bidi;
  wt_session.max_streams_uni_local =
      peer_wt_initial_max_streams_uni_ > 0
          ? peer_wt_initial_max_streams_uni_
          : config_.wt_initial_max_streams_uni;
  if (peer_wt_initial_max_streams_bidi_ > 0) {
    wt_session.received_max_streams_bidi = peer_wt_initial_max_streams_bidi_;
  }
  if (peer_wt_initial_max_streams_uni_ > 0) {
    wt_session.received_max_streams_uni = peer_wt_initial_max_streams_uni_;
  }
  wt_session.max_streams_bidi_remote = config_.wt_initial_max_streams_bidi;
  wt_session.max_streams_uni_remote = config_.wt_initial_max_streams_uni;

  wt_session.peer_max_stream_data_uni =
      peer_wt_initial_max_stream_data_uni_ > 0
          ? peer_wt_initial_max_stream_data_uni_
          : config_.wt_initial_max_stream_data;
  if (peer_wt_initial_max_stream_data_uni_ > 0) {
    wt_session.received_initial_max_stream_data_uni =
        peer_wt_initial_max_stream_data_uni_;
  }
  // 自側開始 bidi: 対向の BIDI_REMOTE
  wt_session.peer_max_stream_data_bidi_local =
      peer_wt_initial_max_stream_data_bidi_remote_ > 0
          ? peer_wt_initial_max_stream_data_bidi_remote_
          : config_.wt_initial_max_stream_data;
  if (peer_wt_initial_max_stream_data_bidi_remote_ > 0) {
    wt_session.received_initial_max_stream_data_bidi_local =
        peer_wt_initial_max_stream_data_bidi_remote_;
  }
  // 対向開始 bidi: 対向の BIDI_LOCAL
  wt_session.peer_max_stream_data_bidi_remote =
      peer_wt_initial_max_stream_data_bidi_local_ > 0
          ? peer_wt_initial_max_stream_data_bidi_local_
          : config_.wt_initial_max_stream_data;
  if (peer_wt_initial_max_stream_data_bidi_local_ > 0) {
    wt_session.received_initial_max_stream_data_bidi_remote =
        peer_wt_initial_max_stream_data_bidi_local_;
  }
}

std::string H2Session::encode_webtransport_init() const {
  // draft-15 Section 4.3.2: Dictionary Structured Field (RFC 8941)
  // 将来キーやセマンティクスが変わる可能性がある
  return "u=" + std::to_string(config_.wt_initial_max_stream_data) +
         ", bl=" + std::to_string(config_.wt_initial_max_stream_data) +
         ", br=" + std::to_string(config_.wt_initial_max_stream_data);
}

bool H2Session::parse_webtransport_init(const std::string& value,
                                        uint64_t& out_u,
                                        uint64_t& out_bl,
                                        uint64_t& out_br,
                                        bool& has_u,
                                        bool& has_bl,
                                        bool& has_br) const {
  has_u = false;
  has_bl = false;
  has_br = false;
  out_u = 0;
  out_bl = 0;
  out_br = 0;

  size_t pos = 0;
  while (pos < value.size()) {
    while (pos < value.size() &&
           (value[pos] == ' ' || value[pos] == '\t' || value[pos] == ',')) {
      ++pos;
    }
    if (pos >= value.size()) {
      break;
    }

    size_t key_start = pos;
    while (pos < value.size() && value[pos] != '=' && value[pos] != ',' &&
           value[pos] != ' ' && value[pos] != '\t') {
      ++pos;
    }
    std::string key = value.substr(key_start, pos - key_start);
    while (pos < value.size() && (value[pos] == ' ' || value[pos] == '\t')) {
      ++pos;
    }
    if (pos >= value.size() || value[pos] != '=') {
      // 未知キーやパラメータなしは無視できるが、形式不正は拒否
      if (!key.empty()) {
        return false;
      }
      continue;
    }
    ++pos;
    while (pos < value.size() && (value[pos] == ' ' || value[pos] == '\t')) {
      ++pos;
    }

    size_t val_start = pos;
    while (pos < value.size() && value[pos] != ',' && value[pos] != ';' &&
           value[pos] != ' ' && value[pos] != '\t') {
      if (!std::isdigit(static_cast<unsigned char>(value[pos]))) {
        return false;
      }
      ++pos;
    }
    if (val_start == pos) {
      return false;
    }
    uint64_t parsed = 0;
    try {
      parsed = std::stoull(value.substr(val_start, pos - val_start));
    } catch (const std::exception&) {
      return false;
    }

    // パラメータ (;...) があればスキップ
    while (pos < value.size() && value[pos] != ',') {
      ++pos;
    }

    if (key == "u") {
      if (has_u) {
        return false;
      }
      has_u = true;
      out_u = parsed;
    } else if (key == "bl") {
      if (has_bl) {
        return false;
      }
      has_bl = true;
      out_bl = parsed;
    } else if (key == "br") {
      if (has_br) {
        return false;
      }
      has_br = true;
      out_br = parsed;
    }
    // 未知キーは MUST ignore (draft-15 Section 4.3.2)
  }

  return true;
}

uint64_t H2Session::peer_send_credit_for_stream(const WtSessionInfo& wt_session,
                                                bool is_unidirectional,
                                                bool is_local) const {
  if (is_unidirectional) {
    return wt_session.peer_max_stream_data_uni;
  }
  if (is_local) {
    return wt_session.peer_max_stream_data_bidi_local;
  }
  return wt_session.peer_max_stream_data_bidi_remote;
}

std::optional<uint64_t> H2Session::advertised_stream_send_credit(
    const WtSessionInfo& wt_session,
    bool is_unidirectional,
    bool is_local) const {
  if (is_unidirectional) {
    return wt_session.received_initial_max_stream_data_uni;
  }
  if (is_local) {
    return wt_session.received_initial_max_stream_data_bidi_local;
  }
  return wt_session.received_initial_max_stream_data_bidi_remote;
}

void H2Session::initialize_stream_send_credit(const WtSessionInfo& wt_session,
                                              WtStreamInfo& info) const {
  info.max_stream_data_local = peer_send_credit_for_stream(
      wt_session, info.is_unidirectional, info.is_local);
  auto capsule_it =
      wt_session.received_max_stream_data_by_id.find(info.stream_id);
  if (capsule_it != wt_session.received_max_stream_data_by_id.end() &&
      capsule_it->second > info.max_stream_data_local) {
    info.max_stream_data_local = capsule_it->second;
  }
}

void H2Session::report_flow_control_error(int32_t session_id,
                                          const std::string& error_message) {
  close_session(session_id, kWtFlowControlError, error_message);
}

void H2Session::report_recv_flow_control_error(
    int32_t session_id,
    uint64_t stream_id,
    const std::string& error_message) {
  // 受信超過は高レベル層への通知のため Error イベントを push してから
  // セッションを閉じる (カプセル値減少の検知は Error を push せず
  // close_session のみ)
  H2Event event;
  event.type = H2EventType::Error;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.error_code = kWtFlowControlError;
  event.error_message = error_message;
  push_event(std::move(event));
  report_flow_control_error(session_id, error_message);
}

void H2Session::report_wt_error(int32_t session_id,
                                const std::string& error_message) {
  // 検知した WT_ERROR をアプリへ通知してからセッションを閉じる。
  // close_session は送信をキューするのみで nghttp2_session_send を呼ばない
  // ため mem_recv コールバック中でも安全であり、即座に is_terminated を
  // 立てて同一 receive() 内の後続カプセルを遮断する
  H2Event event;
  event.type = H2EventType::Error;
  event.session_id = session_id;
  event.error_code = kWtError;
  event.error_message = error_message;
  push_event(std::move(event));
  close_session(session_id, kWtError, error_message);
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
      end_stream_pending_(std::move(other.end_stream_pending_)),
      peer_enable_connect_protocol_(other.peer_enable_connect_protocol_),
      peer_wt_enabled_(other.peer_wt_enabled_),
      peer_wt_initial_max_data_(other.peer_wt_initial_max_data_),
      peer_wt_initial_max_stream_data_uni_(
          other.peer_wt_initial_max_stream_data_uni_),
      peer_wt_initial_max_stream_data_bidi_local_(
          other.peer_wt_initial_max_stream_data_bidi_local_),
      peer_wt_initial_max_stream_data_bidi_remote_(
          other.peer_wt_initial_max_stream_data_bidi_remote_),
      peer_wt_initial_max_streams_uni_(other.peer_wt_initial_max_streams_uni_),
      peer_wt_initial_max_streams_bidi_(
          other.peer_wt_initial_max_streams_bidi_),
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
    end_stream_pending_ = std::move(other.end_stream_pending_);
    peer_enable_connect_protocol_ = other.peer_enable_connect_protocol_;
    peer_wt_enabled_ = other.peer_wt_enabled_;
    peer_wt_initial_max_data_ = other.peer_wt_initial_max_data_;
    peer_wt_initial_max_stream_data_uni_ =
        other.peer_wt_initial_max_stream_data_uni_;
    peer_wt_initial_max_stream_data_bidi_local_ =
        other.peer_wt_initial_max_stream_data_bidi_local_;
    peer_wt_initial_max_stream_data_bidi_remote_ =
        other.peer_wt_initial_max_stream_data_bidi_remote_;
    peer_wt_initial_max_streams_uni_ = other.peer_wt_initial_max_streams_uni_;
    peer_wt_initial_max_streams_bidi_ = other.peer_wt_initial_max_streams_bidi_;
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
  // draft-15 Section 3.1 / 4.3.1 / 11.2
  nghttp2_settings_entry settings[] = {
      {NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS, config_.max_concurrent_streams},
      {NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE, config_.initial_window_size},
      {NGHTTP2_SETTINGS_MAX_FRAME_SIZE, config_.max_frame_size},
      {NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE, config_.max_header_list_size},
      {NGHTTP2_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1},
      {SETTINGS_WT_ENABLED, 1},
      {SETTINGS_WT_INITIAL_MAX_DATA,
       static_cast<uint32_t>(config_.wt_initial_max_data)},
      {SETTINGS_WT_INITIAL_MAX_STREAM_DATA_UNI,
       static_cast<uint32_t>(config_.wt_initial_max_stream_data)},
      {SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_LOCAL,
       static_cast<uint32_t>(config_.wt_initial_max_stream_data)},
      {SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_REMOTE,
       static_cast<uint32_t>(config_.wt_initial_max_stream_data)},
      {SETTINGS_WT_INITIAL_MAX_STREAMS_UNI,
       static_cast<uint32_t>(config_.wt_initial_max_streams_uni)},
      {SETTINGS_WT_INITIAL_MAX_STREAMS_BIDI,
       static_cast<uint32_t>(config_.wt_initial_max_streams_bidi)},
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

int32_t H2Session::connect(const std::string& url,
                           const std::string& origin) {
  if (!session_ || is_server_) {
    return -1;
  }

  // draft-15 Section 3.1: SETTINGS 受信前に CONNECT してはならない
  if (!is_webtransport_ready()) {
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
  // draft-15 Section 3.2
  std::string method = "CONNECT";
  std::string scheme = "https";
  std::string protocol = "webtransport";
  std::string wt_init = encode_webtransport_init();

  std::vector<nghttp2_nv> nva;
  nva.push_back(
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":method")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(method.c_str())),
       7, method.size(), NGHTTP2_NV_FLAG_NONE});
  nva.push_back(
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":scheme")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(scheme.c_str())),
       7, scheme.size(), NGHTTP2_NV_FLAG_NONE});
  nva.push_back(
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":authority")),
       const_cast<uint8_t*>(
           reinterpret_cast<const uint8_t*>(authority.c_str())),
       10, authority.size(), NGHTTP2_NV_FLAG_NONE});
  nva.push_back(
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":path")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(path.c_str())), 5,
       path.size(), NGHTTP2_NV_FLAG_NONE});
  nva.push_back(
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":protocol")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(protocol.c_str())),
       9, protocol.size(), NGHTTP2_NV_FLAG_NONE});

  // draft-15 Section 3.2: Web 文脈では Origin 必須
  if (!origin.empty()) {
    nva.push_back(
        {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>("origin")),
         const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(origin.c_str())),
         6, origin.size(), NGHTTP2_NV_FLAG_NONE});
  }

  // draft-15 Section 4.3.2: 初期フロー制御をヘッダーでも伝える
  nva.push_back(
      {const_cast<uint8_t*>(
           reinterpret_cast<const uint8_t*>("webtransport-init")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(wt_init.c_str())),
       17, wt_init.size(), NGHTTP2_NV_FLAG_NONE});

  // Capsule データ送信用のデータプロバイダー
  nghttp2_data_provider data_prd;
  data_prd.source.ptr = this;
  data_prd.read_callback = data_source_read_callback;

  int32_t stream_id = nghttp2_submit_request(
      session_, nullptr, nva.data(), nva.size(), &data_prd, this);

  if (stream_id < 0) {
    return -1;
  }

  // WebTransport セッションを作成
  WtSessionInfo wt_session;
  wt_session.http2_stream_id = stream_id;
  apply_peer_initial_flow_control(wt_session);
  wt_sessions_[stream_id] = wt_session;

  nghttp2_session_send(session_);

  return stream_id;
}

bool H2Session::accept_session(int32_t session_id) {
  if (!session_ || !is_server_) {
    return false;
  }

  // 200 OK レスポンス
  // draft-15 Section 4.3.2: 応答でも WebTransport-Init を送れる
  std::string status = "200";
  std::string wt_init = encode_webtransport_init();

  nghttp2_nv nva[] = {
      {const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(":status")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(status.c_str())),
       7, status.size(), NGHTTP2_NV_FLAG_NONE},
      {const_cast<uint8_t*>(
           reinterpret_cast<const uint8_t*>("webtransport-init")),
       const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(wt_init.c_str())),
       17, wt_init.size(), NGHTTP2_NV_FLAG_NONE},
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

  // 非 2xx 応答で拒否されたセッションは一度も確立されていない
  // (draft-15 Section 3.2 の「A WebTransport session is established when
  // the server sends a 2xx response」) ため、SessionClosed は発火しない
  // (黙って削除)。削除により、以後の on_stream_close_callback /
  // close_session / send_datagram / send_stream_data / open_stream /
  // reset_stream / stop_sending / drain_session がエントリ不在で塞がれる。
  // 2xx を渡した場合は削除しない
  // (2xx 送出は Section 3.2 の確立条件) が、応答は END_STREAM 付きで送出
  // 済みかつデータプロバイダ未登録のため、以後サーバー側からは送信できない。
  // is_terminated を立てて send_datagram / send_stream_data / reset_stream /
  // stop_sending / drain_session / close_session を塞ぐ (塞がないとカプセルが
  // http2_stream_buffers_ に滞留してワイヤに送出されないまま残る)。
  // close_session は冒頭の is_terminated 確認で塞がれるため、2xx 送出後に
  // 呼んでも WT_CLOSE_SESSION は送出されない (誤用限定の挙動)。
  // is_established は false のまま
  // 残留し、確立済みセッションとしては扱われない。残留の目的は両ハーフ
  // クローズ時の on_stream_close_callback による SessionClosed 発火のため
  // である。
  // accept_session で受理済みのセッションに呼んだ場合は未定義 (誤用)
  if (status_code / 100 != 2) {
    wt_sessions_.erase(session_id);
  } else {
    auto* wt_session = get_wt_session(session_id);
    if (wt_session) {
      wt_session->is_terminated = true;
    }
  }
  // mem_recv コールバック中でも安全なよう、ここでは session_send しない
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
  initialize_stream_send_credit(*wt_session, info);
  info.max_stream_data_remote = config_.wt_initial_max_stream_data;
  wt_session->streams[stream_id] = info;

  // 空の WT_STREAM capsule は送信しない。WT_STREAM capsule は最初のデータ送信
  // (send_stream_data) でストリームを暗黙的に作成する (draft-15 Section 6.4)。
  // 空の WT_STREAM capsule でストリームを開始してから後続の WT_STREAM_FIN
  // capsule でデータを送ると、WebKit の WebTransport over HTTP/2 実装がデータを
  // 破棄するため、実ブラウザとの相互運用性のために空 capsule を送らない。

  return static_cast<int64_t>(stream_id);
}

void H2Session::send_stream_data(int32_t session_id,
                                 uint64_t stream_id,
                                 const std::vector<uint8_t>& data,
                                 bool fin) {
  // 終了したセッション ID への送信を黙って無視する (send_datagram と同じ
  // ガード構成。チェックを send_capsule に置かない理由は send_datagram の
  // コメントを参照)。終了の検知はエントリと終了フラグで行う:
  // WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後・クライアントの
  // 非 2xx 拒否受信後はエントリが削除されて塞がり、ローカル close_session
  // 後は is_terminated で塞がる (サーバー側の reject_session の 2xx 送出も
  // 同様)。塞がないと WT_STREAM / WT_STREAM_FIN capsule が
  // WT_CLOSE_SESSION の後ろに積まれてワイヤへ送出され得る (flush 前) か、
  // http2_stream_buffers_ に残留する (flush 後)
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

  // ストリームが存在しない場合はエラー
  auto stream_it = wt_session->streams.find(stream_id);
  if (stream_it == wt_session->streams.end()) {
    return;
  }

  auto& stream_info = stream_it->second;

  // リセット済みストリームへの送信は塞ぐ (draft-15 Section 6.4 の
  // 「A WT_STREAM capsule MUST NOT be sent after a stream is closed or
  // reset」)。塞ぐのは ResetSent のみとし、FIN 送信後の DataSent 遷移・
  // FIN 後の再送信の塞ぎ・reset_stream の再呼び出しの扱いはスコープ外
  if (stream_info.send_state == StreamState::ResetSent) {
    return;
  }

  // draft-15 Section 6.5 / 6.6: フロー制御超過はセッション閉鎖
  if (wt_session->bytes_sent + data.size() > wt_session->max_data_local ||
      stream_info.bytes_sent + data.size() > stream_info.max_stream_data_local) {
    report_flow_control_error(session_id, "flow control limit exceeded");
    return;
  }

  // WT_STREAM capsule ペイロード: Stream ID + Data
  std::vector<uint8_t> payload = encode_varint(stream_id);
  payload.insert(payload.end(), data.begin(), data.end());

  CapsuleType type = fin ? CapsuleType::WtStreamFin : CapsuleType::WtStream;
  send_capsule(session_id, type, payload);

  // フロー制御更新
  stream_info.bytes_sent += data.size();
  wt_session->bytes_sent += data.size();
}

void H2Session::reset_stream(int32_t session_id,
                             uint64_t stream_id,
                             uint32_t error_code,
                             uint64_t reliable_size) {
  // 終了したセッション ID への送信を黙って無視する (send_stream_data と同じ
  // ガード構成。チェックを send_capsule に置かない理由は send_datagram の
  // コメントを参照)。塞がないと WT_RESET_STREAM capsule が
  // WT_CLOSE_SESSION の後ろに積まれてワイヤへ送出され得る (flush 前) か、
  // http2_stream_buffers_ に残留する (flush 後)
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

  // draft-15 Section 6.2: Reliable Size は送信済みバイト数以下
  auto stream_it = wt_session->streams.find(stream_id);
  if (stream_it != wt_session->streams.end() && reliable_size == 0) {
    reliable_size = stream_it->second.bytes_sent;
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

  // 送信リセットは送信側の終了のみであり、受信側は継続する
  // (draft-15 Section 5.2 の QUIC 状態ミラー)。エントリを erase せず
  // send_state を ResetSent に更新して、受信側の追跡 (bytes_received /
  // recv_state) を維持する (erase すると以後のピアからの WT_STREAM が
  // 新規作成として扱われ、受信追跡が失われる)。エントリは両ハーフ終端後も
  // セッション終了まで保持する (get_stream_ids にはリセット済みストリーム
  // も含まれるようになる)。以後の send_stream_data は send_state の確認で
  // 塞がれる (draft-15 Section 6.4)
  if (stream_it != wt_session->streams.end()) {
    stream_it->second.send_state = StreamState::ResetSent;
  }
}

void H2Session::stop_sending(int32_t session_id,
                             uint64_t stream_id,
                             uint32_t error_code) {
  // 終了したセッション ID と、一度も connect されていないセッション ID への
  // 送信を黙って無視する (send_datagram と同じガード。チェックを
  // send_capsule に置かない理由は send_datagram のコメントを参照)。終了の
  // 検知はエントリと終了フラグで行う: WT_CLOSE_SESSION 受信後・ピアの
  // END_STREAM 受信後・クライアントの非 2xx 拒否受信後はエントリが削除
  // されて塞がり、ローカル close_session 後は is_terminated で塞がる
  // (サーバー側の reject_session の 2xx 送出も同様)。
  // エントリ不在の ID 宛にカプセルをキューすると消えた
  // http2_stream_buffers_ エントリが再生成されて残留し、メモリを保持し
  // 続けるため、ここで返す
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

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
  // 終了したセッション ID と、一度も connect されていないセッション ID への
  // 送信を黙って無視する。セッション終了は draft-15 Section 3.4 の「CONNECT
  // ストリームのクローズ」で定義され、WT_CLOSE_SESSION (Section 6.12) はその
  // 前の終了通知である。受信後は終了を学習した状態とみなし、新たなデータ
  // グラムを送出しない (h3 の Section 6 相当の MUST は h2 には存在しないが、
  // 本対応は仕様強制ではなく実装ポリシー)。終了の検知はエントリと終了フラグ
  // で行う: WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後はエントリが
  // 削除されて塞がり、ローカル close_session 後は is_terminated で塞がる
  // (サーバー側の reject_session の 2xx 送出も同様)。
  // エントリ不在の ID (未 connect・両ハーフクローズ後にエントリが削除された
  // ID) も無視する。チェックは
  // send_capsule ではなくここに置く: send_capsule は close_session
  // (WT_CLOSE_SESSION) / reset_stream / フロー制御応答の送信にも使われて
  // おり、そこにチェックを入れると終了後の後始末カプセルまで塞がれる
  // (send_stream_data / reset_stream / stop_sending / drain_session /
  // close_session はここと同じガードを自前で持つ)。
  // 楽観的送信 (draft-15 Section 3.2 の MAY
  // 「クライアントは応答を待たずに WebTransport カプセルを送信してよい」)
  // は妨げない: クライアントは connect 直後 (200 応答前)・サーバーは
  // CONNECT リクエスト受信時に wt_sessions_ へエントリが挿入され、終了
  // フラグが立っていない (is_established はこの間 false のため、終了状態の
  // 判定に is_established を使うと楽観的送信がすべて無視される)
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

  // クライアントが非 2xx 応答 (拒否) を受けたセッション ID 宛の送信は、
  // 応答受信時に wt_sessions_ から削除されるためエントリ確認で塞がれる
  // (1xx を挟んだ拒否は削除が機能せずエントリが残る既知の制約)。
  // ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送る終了経路 (draft-15
  // Section 3.4 の正規の終了経路) も END_STREAM 検知でエントリが削除される
  // ため塞がれる
  send_capsule(session_id, CapsuleType::Datagram, data);
}

void H2Session::close_session(int32_t session_id,
                              uint32_t error_code,
                              const std::string& error_message) {
  // 終了したセッション ID への呼び出しを黙って無視する (send_datagram と同じ
  // ガード構成。チェックを send_capsule に置かない理由は send_datagram の
  // コメントを参照)。ローカル close_session はエントリを残したまま
  // is_terminated を立てるため、2 回目以降の呼び出しはここで返る (塞がない
  // と WT_CLOSE_SESSION capsule が二重送出され、flush 後は
  // http2_stream_buffers_ に残留する)。終了を学習したセッション ID
  // (WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後・クライアントの
  // 非 2xx 拒否受信後) はエントリが削除されて塞がる (サーバー側の
  // reject_session の 2xx 送出も同様)。send_stream_data のフロー制御違反時
  // (FLOW_CONTROL_ERROR) の内部呼び出しは is_terminated が立つ前のため
  // 塞がれない
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

  // WT_CLOSE_SESSION capsule: Error Code (32bit) + Message
  // draft-15 Section 6.12: Application Error Message は最大 1024 バイト
  std::vector<uint8_t> payload;
  payload.push_back(static_cast<uint8_t>((error_code >> 24) & 0xFF));
  payload.push_back(static_cast<uint8_t>((error_code >> 16) & 0xFF));
  payload.push_back(static_cast<uint8_t>((error_code >> 8) & 0xFF));
  payload.push_back(static_cast<uint8_t>(error_code & 0xFF));

  if (!error_message.empty()) {
    size_t message_len =
        std::min(error_message.size(), kMaxApplicationErrorMessageBytes);
    payload.insert(payload.end(), error_message.begin(),
                   error_message.begin() +
                       static_cast<std::ptrdiff_t>(message_len));
  }

  send_capsule(session_id, CapsuleType::WtCloseSession, payload);

  // ローカルの close_session で終了を学習した状態にする。flush のタイミング
  // に依存せず、以後の send_datagram が WT_CLOSE_SESSION の後ろに積まれる
  // のを防ぐ (send_capsule にはチェックを入れないため、1 回目の呼び出しで
  // 送出される後始末カプセル WT_CLOSE_SESSION 自体は冒頭のガードで塞がれ
  // ない。塞がれるのは 2 回目以降の呼び出しでキューされる WT_CLOSE_SESSION)。
  // 受信側 (handle_wt_close_session) はエントリ削除で同じ効果 (以後の送受信
  // の遮断) を得る。ここではエントリを残したまま is_established も false に
  // し、get_session_ids からの消滅と open_stream の失敗 (セッション終了後の
  // 新規ストリーム開放の抑止) を行う。is_established が false になることで
  // 以後の受信カプセル処理 (on_data_chunk_recv_callback のゲート) も停止する
  // (h3 側の close_stream と同様の終了後後始末)
  wt_session->is_terminated = true;
  wt_session->is_established = false;

  // draft-15 Section 6.12: Capsule 送信後に END_STREAM で half-close する MUST
  // RST_STREAM ではアプリケーション終了を正しく伝えられない
  //
  // nghttp2_session_send は mem_recv コールバック中に呼んではならない。
  // 送信は呼び出し側の send() / receive() 後段に任せる。
  end_stream_pending_.insert(session_id);
  nghttp2_session_resume_data(session_, session_id);
}

bool H2Session::is_webtransport_ready() const {
  // draft-15 Section 3.1
  return peer_enable_connect_protocol_ && peer_wt_enabled_;
}

void H2Session::drain_session(int32_t session_id) {
  // 終了したセッション ID と、一度も connect されていないセッション ID への
  // 送信を黙って無視する (send_datagram と同じガード。stop_sending も同じ
  // 構成。終了の検知はエントリと終了フラグで行う: 終了経路はエントリ削除、
  // ローカル close_session 後は is_terminated で塞がる)。エントリ不在の ID
  // 宛にカプセルをキューすると http2_stream_buffers_ エントリが再生成されて
  // 残留するため、ここで返す
  auto* wt_session = get_wt_session(session_id);
  if (!wt_session || wt_session->is_terminated) {
    return;
  }

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
    case NGHTTP2_SETTINGS:
      // draft-15 Section 3.1 / 4.3.1
      if ((frame->hd.flags & NGHTTP2_FLAG_ACK) == 0) {
        for (size_t i = 0; i < frame->settings.niv; ++i) {
          const nghttp2_settings_entry& entry = frame->settings.iv[i];
          switch (entry.settings_id) {
            case NGHTTP2_SETTINGS_ENABLE_CONNECT_PROTOCOL:
              h2_session->peer_enable_connect_protocol_ = (entry.value == 1);
              break;
            case SETTINGS_WT_ENABLED:
              // draft-15 Section 3.1: 1 超は PROTOCOL_ERROR
              if (entry.value > 1) {
                H2Event event;
                event.type = H2EventType::Error;
                event.error_code = NGHTTP2_PROTOCOL_ERROR;
                event.error_message =
                    "SETTINGS_WT_ENABLED value greater than 1";
                h2_session->push_event(std::move(event));
                h2_session->closed_ = true;
              } else {
                h2_session->peer_wt_enabled_ = (entry.value == 1);
              }
              break;
            case SETTINGS_WT_INITIAL_MAX_DATA:
              h2_session->peer_wt_initial_max_data_ = entry.value;
              break;
            case SETTINGS_WT_INITIAL_MAX_STREAM_DATA_UNI:
              h2_session->peer_wt_initial_max_stream_data_uni_ = entry.value;
              break;
            case SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_LOCAL:
              h2_session->peer_wt_initial_max_stream_data_bidi_local_ =
                  entry.value;
              break;
            case SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_REMOTE:
              h2_session->peer_wt_initial_max_stream_data_bidi_remote_ =
                  entry.value;
              break;
            case SETTINGS_WT_INITIAL_MAX_STREAMS_UNI:
              h2_session->peer_wt_initial_max_streams_uni_ = entry.value;
              break;
            case SETTINGS_WT_INITIAL_MAX_STREAMS_BIDI:
              h2_session->peer_wt_initial_max_streams_bidi_ = entry.value;
              break;
            default:
              break;
          }
        }
      }
      break;

    case NGHTTP2_HEADERS:
      if (frame->headers.cat == NGHTTP2_HCAT_REQUEST &&
          h2_session->is_server_) {
        // サーバー側でリクエストを受信
        auto it = h2_session->pending_headers_.find(stream_id);
        if (it != h2_session->pending_headers_.end()) {
          // WebTransport CONNECT リクエストかチェック
          bool is_connect = false;
          bool is_webtransport = false;
          std::string wt_init_value;
          bool has_wt_init = false;
          for (const auto& [name, value] : it->second) {
            if (name == ":method" && value == "CONNECT") {
              is_connect = true;
            }
            if (name == ":protocol" && value == "webtransport") {
              is_webtransport = true;
            }
            if (name == "webtransport-init") {
              has_wt_init = true;
              wt_init_value = value;
            }
          }
          if (is_connect && is_webtransport) {
            // WebTransport セッション情報を作成
            WtSessionInfo wt_session;
            wt_session.http2_stream_id = stream_id;
            h2_session->apply_peer_initial_flow_control(wt_session);

            // draft-15 Section 4.3: SETTINGS とヘッダーの大きい方を採用
            if (has_wt_init) {
              uint64_t init_u = 0;
              uint64_t init_bl = 0;
              uint64_t init_br = 0;
              bool has_u = false;
              bool has_bl = false;
              bool has_br = false;
              if (!h2_session->parse_webtransport_init(
                      wt_init_value, init_u, init_bl, init_br, has_u, has_bl,
                      has_br)) {
                h2_session->pending_headers_.erase(it);
                h2_session->reject_session(stream_id, 400);
                break;
              }
              // クライアント送信ヘッダー: 受信側 (server) 向けクレジット
              // u = サーバー開始 uni, bl = クライアント開始 bidi,
              // br = サーバー開始 bidi
              if (has_u) {
                wt_session.peer_max_stream_data_uni =
                    std::max(wt_session.peer_max_stream_data_uni, init_u);
                record_received_limit(
                    wt_session.received_initial_max_stream_data_uni, init_u);
              }
              if (has_bl) {
                wt_session.peer_max_stream_data_bidi_remote =
                    std::max(wt_session.peer_max_stream_data_bidi_remote,
                             init_bl);
                record_received_limit(
                    wt_session.received_initial_max_stream_data_bidi_remote,
                    init_bl);
              }
              if (has_br) {
                wt_session.peer_max_stream_data_bidi_local =
                    std::max(wt_session.peer_max_stream_data_bidi_local,
                             init_br);
                record_received_limit(
                    wt_session.received_initial_max_stream_data_bidi_local,
                    init_br);
              }
            }

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
          // レスポンスの :status を取得する (1xx 中間応答もこの分岐に通知
          // される。nghttp2.h の on_begin_headers_callback の docstring と
          // nghttp2_headers_category enum の docstring)
          bool is_success = false;
          std::string status_value;
          std::string wt_init_value;
          bool has_wt_init = false;
          for (const auto& [name, value] : it->second) {
            if (name == ":status") {
              status_value = value;
              if (value == "200") {
                is_success = true;
              }
            }
            if (name == "webtransport-init") {
              has_wt_init = true;
              wt_init_value = value;
            }
          }
          auto* wt_session = h2_session->get_wt_session(stream_id);
          if (is_success && wt_session) {
            // draft-15 Section 4.3: 応答の WebTransport-Init も反映
            if (has_wt_init) {
              uint64_t init_u = 0;
              uint64_t init_bl = 0;
              uint64_t init_br = 0;
              bool has_u = false;
              bool has_bl = false;
              bool has_br = false;
              if (h2_session->parse_webtransport_init(wt_init_value, init_u,
                                                      init_bl, init_br, has_u,
                                                      has_bl, has_br)) {
                // サーバー送信ヘッダー: 受信側 (client) 向けクレジット
                if (has_u) {
                  wt_session->peer_max_stream_data_uni =
                      std::max(wt_session->peer_max_stream_data_uni, init_u);
                  record_received_limit(
                      wt_session->received_initial_max_stream_data_uni, init_u);
                }
                if (has_bl) {
                  wt_session->peer_max_stream_data_bidi_remote = std::max(
                      wt_session->peer_max_stream_data_bidi_remote, init_bl);
                  record_received_limit(
                      wt_session->received_initial_max_stream_data_bidi_remote,
                      init_bl);
                }
                if (has_br) {
                  wt_session->peer_max_stream_data_bidi_local = std::max(
                      wt_session->peer_max_stream_data_bidi_local, init_br);
                  record_received_limit(
                      wt_session->received_initial_max_stream_data_bidi_local,
                      init_br);
                }
              }
            }

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
          } else if (wt_session && !status_value.empty() &&
                     status_value[0] != '1' && status_value[0] != '2') {
            // 非 2xx 応答 (拒否) を受信したセッションは一度も確立されていない
            // (draft-ietf-webtrans-http2-15 Section 3.2 の「A WebTransport
            // session is established when the server sends a 2xx
            // response」)。SessionClosed は発火しない (黙って削除): 一度も
            // 確立されていないセッションの終了通知という意味論が合わない。
            // エントリ削除により、以後の on_stream_close_callback /
            // close_session / send_datagram / send_stream_data / open_stream /
            // reset_stream / stop_sending / drain_session がエントリ不在で
            // 自然に塞がる (二重発火の経路も残らない)。HTTP/2 ストリーム
            // 自体は
            // サーバー側のみが閉じた半開きのまま接続終了まで残る
            // (RST_STREAM 等の後始末は行わない既知の制約)。1xx 中間応答
            // (100-199) は削除対象外: 1xx は中間応答であり拒否ではない
            // (nghttp2 は 1xx で abort せず最終応答を待つ)。1xx を挟んだ
            // 応答の最終応答は NGHTTP2_HCAT_HEADERS で通知され、本分岐で
            // 捕捉されないため wt_sessions_ のエントリと pending_headers_
            // が残る (既知の制約。1xx 後の最終応答の捕捉不能)
            h2_session->wt_sessions_.erase(stream_id);
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

  // ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送って CONNECT ストリーム
  // を閉じた場合のセッション終了検知 (draft-15 Section 3.4 の正規の終了経路)。
  // HEADERS フレームの処理分岐の後に置く: 200 + END_STREAM (受理と同時
  // クローズ) では HCAT_RESPONSE 分岐で is_established が設定された後に検知
  // する必要がある。フレーム種別 (cat) に依存させない (trailers 等の
  // HCAT_HEADERS + END_STREAM も捕捉する)。END_STREAM ビット (0x01) は
  // SETTINGS ACK / PING ACK の ACK ビットと同一の値だが、これらのフレームの
  // stream_id は 0 であり wt_sessions_ にエントリが存在しないため
  // handle_end_stream 冒頭で返る。nghttp2 は DATA / HEADERS 以外のフレーム
  // の未定義フラグをマスクしてから通知するため、0x01 ビットが立って通知
  // されるのは DATA / HEADERS のみである (nghttp2 の実装前提)
  if ((frame->hd.flags & NGHTTP2_FLAG_END_STREAM) != 0) {
    h2_session->handle_end_stream(stream_id);
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
  // ストリームが閉じた場合は END_STREAM 応答 (end_stream_pending_) も不要
  h2_session->end_stream_pending_.erase(stream_id);
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
  bool end_pending =
      h2_session->end_stream_pending_.find(stream_id) !=
      h2_session->end_stream_pending_.end();

  auto it = h2_session->http2_stream_buffers_.find(stream_id);
  if (it == h2_session->http2_stream_buffers_.end() || it->second.empty()) {
    // draft-15 Section 6.12: CLOSE 後は END_STREAM で half-close
    if (end_pending) {
      *data_flags |= NGHTTP2_DATA_FLAG_EOF;
      h2_session->end_stream_pending_.erase(stream_id);
      return 0;
    }
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

  // 送信キューが空で close_session 後なら EOF
  if (it->second.empty() && end_pending) {
    *data_flags |= NGHTTP2_DATA_FLAG_EOF;
    h2_session->end_stream_pending_.erase(stream_id);
  }

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
           nb::arg("origin") = "",
           nb::sig("def connect(self, url: str, origin: str = '') -> int"),
           "WebTransport セッションを開始 (クライアント用)")
      .def("is_webtransport_ready", &H2Session::is_webtransport_ready,
           nb::sig("def is_webtransport_ready(self) -> bool"),
           "対向 SETTINGS で WebTransport over HTTP/2 が有効か")
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
