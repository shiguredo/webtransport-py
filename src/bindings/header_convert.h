/**
 * nghttp2 / nghttp3 のヘッダー表現への変換ヘルパー
 *
 * `std::pair<std::string, std::string>` のリストを各ライブラリの nv 構造体へ
 * 変換する処理は http2 / http3 / webtransport_h2 / webtransport_h3 の各所で
 * 同型に書かれていた。ライブラリごとに型が異なるため、型ごとに 1 つの
 * ヘルパーへ集約する。
 */

#ifndef WEBTRANSPORT_BINDINGS_HEADER_CONVERT_H_
#define WEBTRANSPORT_BINDINGS_HEADER_CONVERT_H_

#include <nghttp2/nghttp2.h>
#include <nghttp3/nghttp3.h>

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace webtransport {
namespace bindings {

/**
 * ヘッダーのリストを nghttp2_nv のベクタへ変換する
 *
 * nghttp2 はヘッダー名・値を非 const ポインタで要求するため const_cast する。
 * 変換元の文字列は呼び出し側が nv の利用中保持する責任を持つ。
 */
inline std::vector<nghttp2_nv> to_nghttp2_nv(
    const std::vector<std::pair<std::string, std::string>>& headers) {
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
  return nva;
}

/**
 * ヘッダーのリストを nghttp3_nv のベクタへ変換する
 *
 * 変換元の文字列は呼び出し側が nv の利用中保持する責任を持つ。
 */
inline std::vector<nghttp3_nv> to_nghttp3_nv(
    const std::vector<std::pair<std::string, std::string>>& headers) {
  std::vector<nghttp3_nv> nva;
  nva.reserve(headers.size());
  for (const auto& [name, value] : headers) {
    nghttp3_nv nv;
    nv.name =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(name.c_str()));
    nv.namelen = name.size();
    nv.value =
        const_cast<uint8_t*>(reinterpret_cast<const uint8_t*>(value.c_str()));
    nv.valuelen = value.size();
    nv.flags = NGHTTP3_NV_FLAG_NONE;
    nva.push_back(nv);
  }
  return nva;
}

/**
 * `scheme://authority/path` 形式の URL から authority と path を取り出す
 *
 * スキームは `://` の位置だけを見て読み飛ばす (値は問わない)。パスが
 * 無い場合は "/" にする。`://` を含まない URL は失敗として false を返す。
 *
 * @param url パースする URL
 * @param authority 出力先 (authority 部分)
 * @param path 出力先 (path 部分。先頭は '/')
 * @return パースに成功したかどうか
 */
inline bool parse_authority_path(const std::string& url,
                                 std::string* authority,
                                 std::string* path) {
  size_t scheme_end = url.find("://");
  if (scheme_end == std::string::npos) {
    return false;
  }

  size_t host_start = scheme_end + 3;
  size_t path_start = url.find('/', host_start);
  if (path_start != std::string::npos) {
    *authority = url.substr(host_start, path_start - host_start);
    *path = url.substr(path_start);
  } else {
    *authority = url.substr(host_start);
    *path = "/";
  }
  return true;
}

}  // namespace bindings
}  // namespace webtransport

#endif  // WEBTRANSPORT_BINDINGS_HEADER_CONVERT_H_
