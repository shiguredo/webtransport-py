/**
 * QUIC 可変長整数 (varint) の共通ヘルパー
 *
 * RFC 9000 Section 16 のエンコード・デコードを 1 箇所に集約する。ngtcp2 を
 * 使う層と手書きでパケットを解析する層で別実装を持たないようにする。
 */

#ifndef WEBTRANSPORT_BINDINGS_QUIC_VARINT_H_
#define WEBTRANSPORT_BINDINGS_QUIC_VARINT_H_

#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace webtransport {
namespace bindings {

// RFC 9000 Section 16: varint で表現できる最大値 (2^62 - 1)
constexpr uint64_t kMaxQuicVarint = (1ULL << 62) - 1;

/**
 * varint のエンコード済みバイト長 (1 / 2 / 4 / 8) を返す
 *
 * ngtcp2_put_uvarintlen と同じ境界計算
 */
inline size_t quic_varint_length(uint64_t value) {
  if (value <= 63) {
    return 1;
  }
  if (value <= 16383) {
    return 2;
  }
  if (value <= 1073741823) {
    return 4;
  }
  return 8;
}

/**
 * 値を QUIC varint としてエンコードする
 *
 * 上位 2 ビットで長さを表すため、2^62 - 1 超は長さ表現が壊れて別の値に
 * デコードされる。呼び出し元は Python 境界の入力検証か Config 生成時検査・
 * カウンタのクランプ済みであることを前提に、防御的に拒否する。
 *
 * @param value エンコードする値
 * @return エンコード結果
 * @throws std::invalid_argument value が 2^62 - 1 超の場合
 */
inline std::vector<uint8_t> encode_quic_varint(uint64_t value) {
  if (value > kMaxQuicVarint) {
    throw std::invalid_argument("varint value must be less than 2^62: " +
                                std::to_string(value));
  }

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

/**
 * 先頭から 1 つの QUIC varint をデコードする
 *
 * 先頭 2 ビットでエンコード長 (1 / 2 / 4 / 8 バイト) が決まる。失敗の表現は
 * 呼び出し側に任せ、ここでは std::nullopt を返す。
 *
 * @param data 入力バッファ
 * @param length 入力バッファ長
 * @return 成功時は (値, 消費バイト数)、長さ不足時は std::nullopt
 */
inline std::optional<std::pair<uint64_t, size_t>> decode_quic_varint(
    const uint8_t* data,
    size_t length) {
  if (length == 0) {
    return std::nullopt;
  }
  uint8_t prefix = data[0] >> 6;
  size_t varint_len = static_cast<size_t>(1) << prefix;
  if (length < varint_len) {
    return std::nullopt;
  }
  uint64_t value = data[0] & 0x3F;
  for (size_t i = 1; i < varint_len; ++i) {
    value = (value << 8) | data[i];
  }
  return std::make_pair(value, varint_len);
}

}  // namespace bindings
}  // namespace webtransport

#endif  // WEBTRANSPORT_BINDINGS_QUIC_VARINT_H_
