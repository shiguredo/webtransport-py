/**
 * Python から C++ へ渡される入力のサイズ上限検査ヘルパー
 *
 * `nb::bytes` を `std::vector` へコピーする前に生の入力サイズを検査する処理は
 * webtransport_h2 / webtransport_h3 の 2 ファイルに同型で書かれていた。上限値と
 * 検査を 1 箇所に集約し、検査の無かった quic / http2 / http3 の 11 経路にも
 * 適用する。
 */

#ifndef WEBTRANSPORT_BINDINGS_PYTHON_INPUT_H_
#define WEBTRANSPORT_BINDINGS_PYTHON_INPUT_H_

#include <cstddef>
#include <stdexcept>
#include <string>

namespace webtransport {
namespace bindings {

/**
 * Python から渡される単回入力の上限 (バイト)
 *
 * nb::bytes から std::vector へコピーする前に検査する。層ごとに閾値が
 * 食い違うと利用者から見てどの API が何バイトまで通るか分からなくなるため、
 * WebTransport の h2 / h3 が先に決めていた値と同値に揃える。h2 は既定の
 * カプセルペイロード上限と同値であることを static_assert で保証しているが、
 * h3 のアプリケーションデータはネイティブ QUIC ストリームと QUIC DATAGRAM で
 * 運ぶため同値の担保対象になる設定が無く、その保証は h2 側にしか無い。
 * これを超える単回入力はアプリ側で分割する前提とする。
 */
inline constexpr size_t kMaxPythonInputBytes = 1024 * 1024;

/**
 * 入力サイズが上限を超えていれば std::invalid_argument を送出する
 *
 * nanobind の既定翻訳により Python 側では ValueError になる。name には
 * 経路を特定できる文字列 (例: "receive data") を渡す。
 */
inline void check_python_input_size(const char* name, size_t size) {
  if (size > kMaxPythonInputBytes) {
    throw std::invalid_argument(std::string(name) + " must be at most " +
                                std::to_string(kMaxPythonInputBytes) +
                                " bytes: got " + std::to_string(size));
  }
}

}  // namespace bindings
}  // namespace webtransport

#endif  // WEBTRANSPORT_BINDINGS_PYTHON_INPUT_H_
