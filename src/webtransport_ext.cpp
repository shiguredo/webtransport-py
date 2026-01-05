#include <nanobind/nanobind.h>

namespace nb = nanobind;

extern void bind_webtransport(nb::module_& m);

NB_MODULE(webtransport_ext, m) {
  bind_webtransport(m);
}
