/**
 * テキスト検証の共通ヘルパー
 *
 * WebTransport over HTTP/2 / HTTP/3 の両層に同一実装の UTF-8 検査が
 * 存在していたため 1 箇所へ集約する。
 */

#ifndef WEBTRANSPORT_BINDINGS_TEXT_UTIL_H_
#define WEBTRANSPORT_BINDINGS_TEXT_UTIL_H_

#include <cstddef>
#include <cstdint>

namespace webtransport {
namespace bindings {

/**
 * RFC 3629 の well-formed UTF-8 かどうかを検査する
 *
 * overlong 符号化、サロゲート (U+D800..U+DFFF)、U+10FFFF 超、不完全
 * シーケンス、非先頭バイトを拒否する。仕様上の "valid UTF-8" の解釈は層に
 * よって異なるため、根拠となる節は呼び出し側でコメントする。
 */
inline bool is_valid_utf8(const uint8_t* data, size_t length) {
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

}  // namespace bindings
}  // namespace webtransport

#endif  // WEBTRANSPORT_BINDINGS_TEXT_UTIL_H_
