# ChordChart analysis core: platform-independent C++ (no Android or iOS code in here).
#
# The same sources build for Android (the app's Gradle build, via the NDK), for iOS later,
# and for the PC, where tests/golden_test.cpp compares every stage with the Python pipeline.
#
# Needs (android/tools/fetch_deps.py puts them in android/third_party):
#   pocketfft (header-only), libsoxr, libopus, Android's MP3 and FDK AAC decoders (source),
#   ONNX Runtime (headers + library)
#
#   CHORDCHART_ORT_INCLUDE   ONNX Runtime headers
#   CHORDCHART_ORT_LIBRARY   ONNX Runtime library to link (onnxruntime.lib / libonnxruntime.so)

cmake_minimum_required(VERSION 3.22)
project(chordchart_core LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(THIRD_PARTY ${CMAKE_CURRENT_SOURCE_DIR}/../third_party CACHE PATH "third-party code")
set(CHORDCHART_ORT_INCLUDE "" CACHE PATH "ONNX Runtime include directory")
set(CHORDCHART_ORT_LIBRARY "" CACHE FILEPATH "ONNX Runtime library")
option(CHORDCHART_BUILD_TESTS "build the PC tests and command-line tool" OFF)

# libsoxr, built as python-soxr 1.1.0 builds it (the Beat This! frontend resamples with it):
# no OpenMP, no libsamplerate bindings, no tests. On Android it is its own shared library
# (LGPL-2.1: linked dynamically).
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(WITH_OPENMP OFF CACHE BOOL "" FORCE)
set(WITH_LSR_BINDINGS OFF CACHE BOOL "" FORCE)
if(ANDROID)
  set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
else()
  set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
endif()
add_subdirectory(${THIRD_PARTY}/soxr ${CMAKE_BINARY_DIR}/soxr EXCLUDE_FROM_ALL)

# libopus 1.5, fixed-point as AOSP builds it (external/libopus: -DFIXED_POINT), so Opus
# decodes in-process exactly as Android's own decoder. Static: BSD-3-Clause.
set(OPUS_FIXED_POINT ON CACHE BOOL "" FORCE)
# Plain C: 1.5's CMake enables fixed-point NEON code it doesn't build on arm64. Fixed-point
# libopus is integer arithmetic and its SIMD paths are bit-exact with the C, so the output
# is the same; decoding Opus costs little either way.
set(OPUS_DISABLE_INTRINSICS ON CACHE BOOL "" FORCE)
set(OPUS_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_SHARED_LIBRARY OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_PKG_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_CMAKE_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(_shared ${BUILD_SHARED_LIBS})
set(BUILD_SHARED_LIBS OFF)
add_subdirectory(${THIRD_PARTY}/opus ${CMAKE_BINARY_DIR}/opus EXCLUDE_FROM_ALL)
set(BUILD_SHARED_LIBS ${_shared})
# soxr's own install/export rules want a relative prefix; not needed here.

# Android 15's own software MP3 and AAC decoders (AOSP android-15.0.0_r1), built as AOSP
# builds them (Android.bp: plain C on arm64 and x86_64, the 32-bit ARM assembly unused),
# so MP3 and AAC decode in-process exactly as c2.android.mp3/aac.decoder do. Both are
# fixed-point; AOSP compiles everything with -O2 and -ffp-contract=off.
file(GLOB PVMP3_SOURCES ${THIRD_PARTY}/aosp-mp3dec/src/*.cpp)
add_library(pvmp3 STATIC ${PVMP3_SOURCES})
# its API header includes src/pvmp3_dec_defs.h
target_include_directories(pvmp3 SYSTEM PUBLIC ${THIRD_PARTY}/aosp-mp3dec/include ${THIRD_PARTY}/aosp-mp3dec/src)
if(MSVC)
  target_compile_options(pvmp3 PRIVATE /FI${CMAKE_CURRENT_SOURCE_DIR}/shims/pvmp3_defs.h)
else()
  target_compile_options(pvmp3 PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/shims/pvmp3_defs.h)
endif()

set(FDK_LIBS libAACdec libFDK libSYS libMpegTPDec libSBRdec libArithCoding libDRCdec libSACdec libPCMutils)
set(FDK_SOURCES)
set(FDK_INCLUDES)
foreach(lib ${FDK_LIBS})
  file(GLOB lib_sources ${THIRD_PARTY}/aosp-aac/${lib}/src/*.cpp)
  list(APPEND FDK_SOURCES ${lib_sources})
  list(APPEND FDK_INCLUDES ${THIRD_PARTY}/aosp-aac/${lib}/include)
endforeach()
add_library(fdkaac STATIC ${FDK_SOURCES} ${THIRD_PARTY}/aosp-c2aac/DrcPresModeWrap.cpp)
target_include_directories(fdkaac SYSTEM PUBLIC ${FDK_INCLUDES} ${THIRD_PARTY}/aosp-c2aac)
target_include_directories(fdkaac PRIVATE shims)
target_compile_definitions(fdkaac PRIVATE SUPPRESS_BUILD_DATE_INFO)

foreach(t pvmp3 fdkaac)
  if(MSVC)
    # AOSP builds these with clang only; for the PC tests, drop GCC attributes
    target_compile_options(${t} PRIVATE /W0 /fp:precise)
    target_compile_options(${t} PRIVATE /FI${CMAKE_CURRENT_SOURCE_DIR}/shims/msvc_attributes.h)
  else()
    target_compile_options(${t} PRIVATE -w -O2 -ffp-contract=off)
  endif()
endforeach()

add_library(chordchart_core STATIC
  src/npy.cpp
  src/json.cpp
  src/tables.cpp
  src/spectrogram.cpp
  src/resample.cpp
  src/onnx.cpp
  src/beat_this.cpp
  src/dbn.cpp
  src/crf.cpp
  src/chart.cpp
  src/analyzer.cpp
  src/audio.cpp
  src/demux.cpp
  src/demux_mpeg.cpp
  src/decode.cpp
  src/decode_mpeg.cpp
)
target_include_directories(chordchart_core
  PUBLIC include
  PRIVATE src)
# third-party headers: SYSTEM, so their warnings don't bury ours
target_include_directories(chordchart_core SYSTEM
  PRIVATE ${THIRD_PARTY}/pocketfft ${THIRD_PARTY}/soxr/src ${CHORDCHART_ORT_INCLUDE})
target_link_libraries(chordchart_core PUBLIC soxr opus pvmp3 fdkaac ${CHORDCHART_ORT_LIBRARY})
if(MSVC)
  target_compile_options(chordchart_core PRIVATE /W4 /permissive- /utf-8 /fp:precise)
else()
  # No -ffast-math: the stages must round like numpy.
  target_compile_options(chordchart_core PRIVATE -Wall -Wextra -ffp-contract=off)
endif()

if(CHORDCHART_BUILD_TESTS)
  add_executable(golden_test tests/golden_test.cpp)
  target_include_directories(golden_test PRIVATE src ${CHORDCHART_ORT_INCLUDE})
  target_link_libraries(golden_test PRIVATE chordchart_core)
  add_executable(chordchart_cli tools/cli.cpp)
  target_include_directories(chordchart_cli PRIVATE src)
  target_link_libraries(chordchart_cli PRIVATE chordchart_core)
  if(MSVC)
    foreach(t golden_test chordchart_cli)
      target_compile_options(${t} PRIVATE /utf-8)
    endforeach()
  endif()
endif()
