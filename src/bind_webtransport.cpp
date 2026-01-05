/**
 * WebTransport バインディング
 *
 * Sans-IO スタイルの QUIC/HTTP2/HTTP3/WebTransport 実装
 */

#include <nanobind/nanobind.h>

#include "bindings/http2.h"
#include "bindings/http3.h"
#include "bindings/quic.h"
#include "bindings/webtransport_h2.h"
#include "bindings/webtransport_h3.h"

namespace nb = nanobind;

void bind_webtransport(nb::module_& m) {
  m.doc() = "Sans-IO WebTransport/HTTP3/HTTP2/QUIC library";

  // QUIC バインディング
  webtransport::quic::bind_quic(m);

  // HTTP/2 バインディング
  webtransport::http2::bind_http2(m);

  // HTTP/3 バインディング
  webtransport::http3::bind_http3(m);

  // WebTransport over HTTP/3 バインディング
  webtransport::h3::bind_webtransport_h3(m);

  // WebTransport over HTTP/2 バインディング
  webtransport::h2::bind_webtransport_h2(m);
}
