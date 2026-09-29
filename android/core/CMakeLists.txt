# ChordChart analysis core: platform-independent C++ (no Android or iOS code in here).
#
# The same sources build for Android (the app's Gradle build, via the NDK), for iOS later,
# and for the PC, where tests/golden_test.cpp compares every stage with the Python pipeline.
#
# Needs (android/tools/fetch_deps.py puts them in android/third_party):
#   pocketfft (header-only), libsoxr (source), ONNX Runtime (headers + library)
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
# soxr's own install/export rules want a relative prefix; not needed here.

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
)
target_include_directories(chordchart_core
  PUBLIC include
  PRIVATE src)
# third-party headers: SYSTEM, so their warnings don't bury ours
target_include_directories(chordchart_core SYSTEM
  PRIVATE ${THIRD_PARTY}/pocketfft ${THIRD_PARTY}/soxr/src ${CHORDCHART_ORT_INCLUDE})
target_link_libraries(chordchart_core PUBLIC soxr ${CHORDCHART_ORT_LIBRARY})
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
