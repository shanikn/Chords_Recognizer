# The app's native library: the platform-independent core (android/core) plus a thin JNI
# layer (jni.cpp) — the only Android-specific C++.

cmake_minimum_required(VERSION 3.22)
project(chordchart_android LANGUAGES C CXX)

set(ANDROID_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../../../..)
set(THIRD_PARTY ${ANDROID_ROOT}/third_party CACHE PATH "" FORCE)
set(ORT ${THIRD_PARTY}/onnxruntime-android)
set(CHORDCHART_ORT_INCLUDE ${ORT}/headers CACHE PATH "" FORCE)
set(CHORDCHART_ORT_LIBRARY ${ORT}/jni/${ANDROID_ABI}/libonnxruntime.so CACHE FILEPATH "" FORCE)

# The analysis is numeric work: optimise it in debug builds too (a debug APK is what gets
# sideloaded and benchmarked; -O0 made the spectrograms and the DBN several times slower).
# -g keeps the symbols for debugging crashes.
# Release builds (AGP uses RelWithDebInfo) get exactly the same flags, so a release APK
# computes bit for bit what the verified debug builds do (no -O3, no NDEBUG differences).
foreach(config DEBUG RELEASE RELWITHDEBINFO)
    set(CMAKE_C_FLAGS_${config} "-O2 -g")
    set(CMAKE_CXX_FLAGS_${config} "-O2 -g")
endforeach()

add_subdirectory(${ANDROID_ROOT}/core ${CMAKE_BINARY_DIR}/core)

add_library(chordchart SHARED jni.cpp)
target_link_libraries(chordchart PRIVATE chordchart_core log)
target_compile_options(chordchart PRIVATE -Wall -Wextra)
