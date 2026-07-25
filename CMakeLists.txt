cmake_minimum_required(VERSION 4.2)
project(webtransport_py LANGUAGES C CXX)

# プラットフォームチェック
if(NOT APPLE AND NOT UNIX AND NOT WIN32)
  message(FATAL_ERROR "This package supports macOS, Linux (Ubuntu), and Windows.")
endif()

# ExternalProject の依存関係は _deps 配下で管理
if(DEFINED SKBUILD_PROJECT_DIR)
  set(PROJECT_ROOT "${SKBUILD_PROJECT_DIR}")
else()
  set(PROJECT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
endif()

set(DEPS_DIR "${PROJECT_ROOT}/_deps")
message(STATUS "Dependencies directory: ${DEPS_DIR}")

# Python / nanobind
find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
find_package(nanobind CONFIG REQUIRED)

# 依存関係用の ExternalProject
include(ExternalProject)

# JSON から依存関係のバージョンを読み込み
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/deps.json DEPS_JSON)

# deps.json の tag / branch / ref を解析する
# tag または branch の場合は shallow clone、ref (コミットハッシュ) の場合はフル clone
function(parse_deps_git_ref DEPS_JSON_CONTENT DEP_NAME OUT_REPOSITORY OUT_GIT_TAG OUT_USE_SHALLOW OUT_VERSION)
  string(JSON REPOSITORY GET ${DEPS_JSON_CONTENT} ${DEP_NAME} url)
  string(JSON GIT_TAG ERROR_VARIABLE TAG_ERROR GET ${DEPS_JSON_CONTENT} ${DEP_NAME} tag)
  if(TAG_ERROR)
    string(JSON GIT_TAG ERROR_VARIABLE BRANCH_ERROR GET ${DEPS_JSON_CONTENT} ${DEP_NAME} branch)
    if(BRANCH_ERROR)
      string(JSON GIT_TAG GET ${DEPS_JSON_CONTENT} ${DEP_NAME} ref)
      set(USE_SHALLOW FALSE)
    else()
      set(USE_SHALLOW TRUE)
    endif()
  else()
    set(USE_SHALLOW TRUE)
  endif()
  set(${OUT_REPOSITORY} "${REPOSITORY}" PARENT_SCOPE)
  set(${OUT_GIT_TAG} "${GIT_TAG}" PARENT_SCOPE)
  set(${OUT_USE_SHALLOW} "${USE_SHALLOW}" PARENT_SCOPE)
  set(${OUT_VERSION} "${GIT_TAG}" PARENT_SCOPE)
endfunction()

# ngtcp2 の設定を解析
parse_deps_git_ref("${DEPS_JSON}" ngtcp2 NGTCP2_GIT_REPOSITORY NGTCP2_GIT_TAG NGTCP2_USE_SHALLOW NGTCP2_VERSION)

# nghttp3 の設定を解析
parse_deps_git_ref("${DEPS_JSON}" nghttp3 NGHTTP3_GIT_REPOSITORY NGHTTP3_GIT_TAG NGHTTP3_USE_SHALLOW NGHTTP3_VERSION)

# nghttp2 の設定を解析
parse_deps_git_ref("${DEPS_JSON}" nghttp2 NGHTTP2_GIT_REPOSITORY NGHTTP2_GIT_TAG NGHTTP2_USE_SHALLOW NGHTTP2_VERSION)

# ExternalProject ビルド用の並列ビルドジョブ数を決定
if(DEFINED ENV{CMAKE_BUILD_PARALLEL_LEVEL})
  set(PARALLEL_JOBS "$ENV{CMAKE_BUILD_PARALLEL_LEVEL}")
else()
  include(ProcessorCount)
  ProcessorCount(NCORES)
  if(NOT NCORES OR NCORES EQUAL 0)
    set(NCORES 2)
  endif()
  set(PARALLEL_JOBS "${NCORES}")
endif()

# AWS-LC の設定を解析
parse_deps_git_ref("${DEPS_JSON}" aws-lc AWS_LC_GIT_REPOSITORY AWS_LC_GIT_TAG AWS_LC_USE_SHALLOW AWS_LC_VERSION)

# ========== AWS-LC ==========
# ngtcp2 の BoringSSL crypto ヘルパーは AWS-LC でも利用する
message(STATUS "Setting up AWS-LC...")
set(AWS_LC_SOURCE_DIR "${DEPS_DIR}/aws-lc/${AWS_LC_VERSION}/source")
set(AWS_LC_BUILD_DIR "${DEPS_DIR}/aws-lc/${AWS_LC_VERSION}/build")

# AWS-LC の静的ライブラリパス
# ssl / crypto はサブディレクトリに出力される
# Windows の MSVC マルチ構成ジェネレータでは Release/<target>.lib に出力される
if(WIN32)
  set(AWS_LC_SSL_LIB ${AWS_LC_BUILD_DIR}/ssl/Release/ssl.lib)
  set(AWS_LC_CRYPTO_LIB ${AWS_LC_BUILD_DIR}/crypto/Release/crypto.lib)
else()
  set(AWS_LC_SSL_LIB ${AWS_LC_BUILD_DIR}/ssl/libssl.a)
  set(AWS_LC_CRYPTO_LIB ${AWS_LC_BUILD_DIR}/crypto/libcrypto.a)
endif()

# ビルド済みかチェック
if(EXISTS ${AWS_LC_SSL_LIB} AND EXISTS ${AWS_LC_CRYPTO_LIB})
  message(STATUS "AWS-LC already built: ${AWS_LC_SSL_LIB}")
  add_custom_target(aws_lc_build)
else()
  message(STATUS "Building AWS-LC...")

  set(AWS_LC_CMAKE_ARGS
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DBUILD_SHARED_LIBS=OFF
    -DBUILD_TESTING=OFF
    -DDISABLE_GO=ON
    -DDISABLE_PERL=ON
  )

  # Windows の場合は静的 CRT を使用する
  if(WIN32)
    list(APPEND AWS_LC_CMAKE_ARGS
      -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded
    )
  endif()

  ExternalProject_Add(
    aws_lc_build
    GIT_REPOSITORY ${AWS_LC_GIT_REPOSITORY}
    GIT_TAG ${AWS_LC_GIT_TAG}
    GIT_SHALLOW ${AWS_LC_USE_SHALLOW}
    SOURCE_DIR ${AWS_LC_SOURCE_DIR}
    BINARY_DIR ${AWS_LC_BUILD_DIR}
    STAMP_DIR ${AWS_LC_BUILD_DIR}/stamp
    TMP_DIR ${AWS_LC_BUILD_DIR}/tmp
    UPDATE_COMMAND ""
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${AWS_LC_CMAKE_ARGS}
    BUILD_COMMAND ${CMAKE_COMMAND} --build ${AWS_LC_BUILD_DIR} --config Release --parallel ${PARALLEL_JOBS}
    INSTALL_COMMAND ""
    BUILD_BYPRODUCTS ${AWS_LC_SSL_LIB} ${AWS_LC_CRYPTO_LIB}
  )
endif()

message(STATUS "AWS-LC include dir: ${AWS_LC_SOURCE_DIR}/include")
message(STATUS "AWS-LC SSL library: ${AWS_LC_SSL_LIB}")
message(STATUS "AWS-LC Crypto library: ${AWS_LC_CRYPTO_LIB}")

# ========== ngtcp2 ==========
message(STATUS "Setting up ngtcp2...")
set(NGTCP2_SOURCE_DIR "${DEPS_DIR}/ngtcp2/${NGTCP2_VERSION}/source")
set(NGTCP2_BUILD_DIR "${DEPS_DIR}/ngtcp2/${NGTCP2_VERSION}/build")
set(NGTCP2_INSTALL_DIR "${DEPS_DIR}/ngtcp2/${NGTCP2_VERSION}/install")

# 静的ライブラリパス設定
if(WIN32)
  set(NGTCP2_LIB ${NGTCP2_INSTALL_DIR}/lib/ngtcp2.lib)
  set(NGTCP2_CRYPTO_LIB ${NGTCP2_INSTALL_DIR}/lib/ngtcp2_crypto_boringssl.lib)
else()
  set(NGTCP2_LIB ${NGTCP2_INSTALL_DIR}/lib/libngtcp2.a)
  set(NGTCP2_CRYPTO_LIB ${NGTCP2_INSTALL_DIR}/lib/libngtcp2_crypto_boringssl.a)
endif()

# ビルド済みかチェック
if(EXISTS ${NGTCP2_LIB} AND EXISTS ${NGTCP2_CRYPTO_LIB})
  message(STATUS "ngtcp2 already built: ${NGTCP2_LIB}")
  add_custom_target(ngtcp2_build)
else()
  message(STATUS "Building ngtcp2 with AWS-LC...")

  # Windows の場合は AWS-LC が ws2_32 と advapi32 を必要とする
  # ngtcp2 は AWS-LC を BoringSSL 互換バックエンドとして扱う
  if(WIN32)
    set(AWS_LC_LIBS_FOR_NGTCP2 "${AWS_LC_SSL_LIB}$<SEMICOLON>${AWS_LC_CRYPTO_LIB}$<SEMICOLON>ws2_32$<SEMICOLON>advapi32")
  else()
    set(AWS_LC_LIBS_FOR_NGTCP2 "${AWS_LC_SSL_LIB}$<SEMICOLON>${AWS_LC_CRYPTO_LIB}")
  endif()

  set(NGTCP2_CMAKE_ARGS
    -DCMAKE_INSTALL_PREFIX=${NGTCP2_INSTALL_DIR}
    -DCMAKE_BUILD_TYPE=Release
    -DENABLE_LIB_ONLY=ON
    -DENABLE_STATIC_LIB=ON
    -DENABLE_SHARED_LIB=OFF
    -DBUILD_TESTING=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_OPENSSL=OFF
    -DENABLE_BORINGSSL=ON
    -DBORINGSSL_INCLUDE_DIR=${AWS_LC_SOURCE_DIR}/include
    -DBORINGSSL_LIBRARIES=${AWS_LC_LIBS_FOR_NGTCP2}
  )

  # Windows の場合は静的 CRT を使用し、AWS-LC 検出をスキップ
  # check_symbol_exists は値なしマクロ (#define OPENSSL_IS_AWSLC) を検出できないため
  if(WIN32)
    list(APPEND NGTCP2_CMAKE_ARGS
      -DENABLE_STATIC_CRT=ON
      -DHAVE_AWSLC:BOOL=ON
    )
  endif()

  ExternalProject_Add(
    ngtcp2_build
    GIT_REPOSITORY ${NGTCP2_GIT_REPOSITORY}
    GIT_TAG ${NGTCP2_GIT_TAG}
    GIT_SHALLOW ${NGTCP2_USE_SHALLOW}
    GIT_SUBMODULES_RECURSE TRUE
    SOURCE_DIR ${NGTCP2_SOURCE_DIR}
    BINARY_DIR ${NGTCP2_BUILD_DIR}
    STAMP_DIR ${NGTCP2_BUILD_DIR}/stamp
    TMP_DIR ${NGTCP2_BUILD_DIR}/tmp
    UPDATE_COMMAND ""
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${NGTCP2_CMAKE_ARGS}
    BUILD_COMMAND ${CMAKE_COMMAND} --build ${NGTCP2_BUILD_DIR} --config Release --parallel ${PARALLEL_JOBS}
    INSTALL_COMMAND ${CMAKE_COMMAND} --build ${NGTCP2_BUILD_DIR} --config Release --target install
    BUILD_BYPRODUCTS ${NGTCP2_LIB} ${NGTCP2_CRYPTO_LIB}
    DEPENDS aws_lc_build
  )
endif()

# ========== nghttp3 ==========
message(STATUS "Setting up nghttp3...")
set(NGHTTP3_SOURCE_DIR "${DEPS_DIR}/nghttp3/${NGHTTP3_VERSION}/source")
set(NGHTTP3_BUILD_DIR "${DEPS_DIR}/nghttp3/${NGHTTP3_VERSION}/build")
set(NGHTTP3_INSTALL_DIR "${DEPS_DIR}/nghttp3/${NGHTTP3_VERSION}/install")

# 静的ライブラリパス設定
if(WIN32)
  set(NGHTTP3_LIB ${NGHTTP3_INSTALL_DIR}/lib/nghttp3.lib)
else()
  set(NGHTTP3_LIB ${NGHTTP3_INSTALL_DIR}/lib/libnghttp3.a)
endif()

# ビルド済みかチェック
if(EXISTS ${NGHTTP3_LIB})
  message(STATUS "nghttp3 already built: ${NGHTTP3_LIB}")
  add_custom_target(nghttp3_build)
else()
  message(STATUS "Building nghttp3...")

  set(NGHTTP3_CMAKE_ARGS
    -DCMAKE_INSTALL_PREFIX=${NGHTTP3_INSTALL_DIR}
    -DCMAKE_BUILD_TYPE=Release
    -DENABLE_LIB_ONLY=ON
    -DENABLE_STATIC_LIB=ON
    -DENABLE_SHARED_LIB=OFF
    -DBUILD_TESTING=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  )

  # Windows の場合は静的 CRT を使用
  if(WIN32)
    list(APPEND NGHTTP3_CMAKE_ARGS -DENABLE_STATIC_CRT=ON)
  endif()

  ExternalProject_Add(
    nghttp3_build
    GIT_REPOSITORY ${NGHTTP3_GIT_REPOSITORY}
    GIT_TAG ${NGHTTP3_GIT_TAG}
    GIT_SHALLOW ${NGHTTP3_USE_SHALLOW}
    SOURCE_DIR ${NGHTTP3_SOURCE_DIR}
    BINARY_DIR ${NGHTTP3_BUILD_DIR}
    STAMP_DIR ${NGHTTP3_BUILD_DIR}/stamp
    TMP_DIR ${NGHTTP3_BUILD_DIR}/tmp
    UPDATE_COMMAND ""
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${NGHTTP3_CMAKE_ARGS}
    BUILD_COMMAND ${CMAKE_COMMAND} --build ${NGHTTP3_BUILD_DIR} --config Release --parallel ${PARALLEL_JOBS}
    INSTALL_COMMAND ${CMAKE_COMMAND} --build ${NGHTTP3_BUILD_DIR} --config Release --target install
    BUILD_BYPRODUCTS ${NGHTTP3_LIB}
  )
endif()

# ========== nghttp2 ==========
message(STATUS "Setting up nghttp2...")
set(NGHTTP2_SOURCE_DIR "${DEPS_DIR}/nghttp2/${NGHTTP2_VERSION}/source")
set(NGHTTP2_BUILD_DIR "${DEPS_DIR}/nghttp2/${NGHTTP2_VERSION}/build")
set(NGHTTP2_INSTALL_DIR "${DEPS_DIR}/nghttp2/${NGHTTP2_VERSION}/install")

# 静的ライブラリパス設定
if(WIN32)
  set(NGHTTP2_LIB ${NGHTTP2_INSTALL_DIR}/lib/nghttp2.lib)
else()
  set(NGHTTP2_LIB ${NGHTTP2_INSTALL_DIR}/lib/libnghttp2.a)
endif()

# ビルド済みかチェック
if(EXISTS ${NGHTTP2_LIB})
  message(STATUS "nghttp2 already built: ${NGHTTP2_LIB}")
  add_custom_target(nghttp2_build)
else()
  message(STATUS "Building nghttp2...")

  set(NGHTTP2_CMAKE_ARGS
    -DCMAKE_INSTALL_PREFIX=${NGHTTP2_INSTALL_DIR}
    -DCMAKE_BUILD_TYPE=Release
    -DENABLE_LIB_ONLY=ON
    -DBUILD_STATIC_LIBS=ON
    -DBUILD_SHARED_LIBS=OFF
    -DBUILD_TESTING=OFF
    -DENABLE_DOC=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  )

  # Windows の場合は静的 CRT を使用
  if(WIN32)
    list(APPEND NGHTTP2_CMAKE_ARGS -DENABLE_STATIC_CRT=ON)
  endif()

  ExternalProject_Add(
    nghttp2_build
    GIT_REPOSITORY ${NGHTTP2_GIT_REPOSITORY}
    GIT_TAG ${NGHTTP2_GIT_TAG}
    GIT_SHALLOW ${NGHTTP2_USE_SHALLOW}
    SOURCE_DIR ${NGHTTP2_SOURCE_DIR}
    BINARY_DIR ${NGHTTP2_BUILD_DIR}
    STAMP_DIR ${NGHTTP2_BUILD_DIR}/stamp
    TMP_DIR ${NGHTTP2_BUILD_DIR}/tmp
    UPDATE_COMMAND ""
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${NGHTTP2_CMAKE_ARGS}
    BUILD_COMMAND ${CMAKE_COMMAND} --build ${NGHTTP2_BUILD_DIR} --config Release --parallel ${PARALLEL_JOBS}
    INSTALL_COMMAND ${CMAKE_COMMAND} --build ${NGHTTP2_BUILD_DIR} --config Release --target install
    BUILD_BYPRODUCTS ${NGHTTP2_LIB}
  )
endif()

# nanobind モジュール
nanobind_add_module(webtransport_ext
  NB_DOMAIN "webtransport"
  FREE_THREADED
  src/webtransport_ext.cpp
  src/bind_webtransport.cpp
  src/bindings/quic.cpp
  src/bindings/http2.cpp
  src/bindings/http3.cpp
  src/bindings/webtransport_h3.cpp
  src/bindings/webtransport_h2.cpp
)

# ビルド順序を保証するため依存関係を追加
add_dependencies(webtransport_ext aws_lc_build ngtcp2_build nghttp3_build nghttp2_build)

# インクルードディレクトリ
target_include_directories(webtransport_ext PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${NGTCP2_INSTALL_DIR}/include
  ${NGHTTP3_INSTALL_DIR}/include
  ${NGHTTP2_INSTALL_DIR}/include
  ${AWS_LC_SOURCE_DIR}/include
)

# ライブラリをリンク
target_link_libraries(webtransport_ext PRIVATE
  ${NGTCP2_CRYPTO_LIB}
  ${NGTCP2_LIB}
  ${NGHTTP3_LIB}
  ${NGHTTP2_LIB}
  ${AWS_LC_SSL_LIB}
  ${AWS_LC_CRYPTO_LIB}
)

# プラットフォーム固有の設定
if(APPLE)
  target_link_libraries(webtransport_ext PRIVATE
    "-framework CoreFoundation"
    "-framework Security"
  )
elseif(WIN32)
  # 文字コードを utf-8 として扱うのと、シンボルテーブル数を増やす
  target_compile_options(webtransport_ext PRIVATE /utf-8 /bigobj)
  # CRT ライブラリを静的リンクさせる
  set_property(TARGET webtransport_ext PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  # nanobind ターゲットにも同じ設定を適用
  foreach(NB_TARGET nanobind-static nanobind-static-ft)
    if(TARGET ${NB_TARGET})
      set_property(TARGET ${NB_TARGET} PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    endif()
  endforeach()
  target_compile_definitions(webtransport_ext PRIVATE
    _CONSOLE
    _WIN32_WINNT=0x0A00
    NOMINMAX
    WIN32_LEAN_AND_MEAN
    NGTCP2_STATICLIB
    NGHTTP2_STATICLIB
    NGHTTP3_STATICLIB
  )
  # Windows ライブラリをリンク
  target_link_libraries(webtransport_ext PRIVATE
    ws2_32
    crypt32
  )
endif()

# コンパイルオプション
if(NOT WIN32)
  target_compile_options(webtransport_ext PRIVATE -fvisibility=hidden)
endif()

# 型スタブ (.pyi) ファイルの自動生成
nanobind_add_stub(
  webtransport_stub
  MODULE webtransport_ext
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/webtransport_ext.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
  MARKER_FILE py.typed
)

nanobind_add_stub(
  quic_stub
  MODULE webtransport_ext.quic
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/quic.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
)

nanobind_add_stub(
  http2_stub
  MODULE webtransport_ext.http2
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/http2.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
)

nanobind_add_stub(
  http3_stub
  MODULE webtransport_ext.http3
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/http3.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
)

nanobind_add_stub(
  h3_stub
  MODULE webtransport_ext.h3
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/h3.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
)

nanobind_add_stub(
  h2_stub
  MODULE webtransport_ext.h2
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/h2.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:webtransport_ext>
  DEPENDS webtransport_ext
)

# __init__.pyi を生成 (型エクスポート用)
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/__init__.pyi
"\"\"\"WebTransport (Sans-IO) type stubs\"\"\"

from . import quic as quic
from . import http2 as http2
from . import http3 as http3
from . import h3 as h3
from . import h2 as h2

__all__: list[str]
")

# Wheel に配置するためのインストール先を指定
set(PY_PACKAGE "webtransport")
install(TARGETS webtransport_ext
  LIBRARY DESTINATION ${PY_PACKAGE}
  RUNTIME DESTINATION ${PY_PACKAGE}
)

# 型スタブファイルとマーカーファイルをインストール
install(FILES
  ${CMAKE_CURRENT_BINARY_DIR}/webtransport_ext.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/quic.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/http2.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/http3.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/h3.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/h2.pyi
  ${CMAKE_CURRENT_BINARY_DIR}/__init__.pyi
  DESTINATION ${PY_PACKAGE}
)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/py.typed DESTINATION ${PY_PACKAGE})
