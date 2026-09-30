// JNI bridge between the Kotlin app and the platform-independent core. Kotlin side:
// io.github.shanikn.chordchart.Native.

#include <jni.h>

#include <cstring>
#include <atomic>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <sys/stat.h>
#include <unistd.h>

#include "chordchart/analyzer.hpp"
#include "chordchart/audio.hpp"
#include "chordchart/decode.hpp"
#include "chordchart/demux.hpp"

using namespace chordchart;

namespace {

std::string to_string(JNIEnv* env, jstring s) {
    const char* chars = env->GetStringUTFChars(s, nullptr);
    std::string out(chars);
    env->ReleaseStringUTFChars(s, chars);
    return out;
}

void throw_java(JNIEnv* env, const char* cls, const char* message) {
    jclass c = env->FindClass(cls);
    if (c) env->ThrowNew(c, message);
}

// Runs fn, turning C++ exceptions into Java ones: an AnalysisError (a problem with the
// audio, shown to the user as is) becomes AnalysisException, anything else RuntimeException.
template <typename Fn>
auto guarded(JNIEnv* env, Fn&& fn) -> decltype(fn()) {
    try {
        return fn();
    } catch (const AnalysisError& e) {
        throw_java(env, "io/github/shanikn/chordchart/AnalysisException", e.what());
    } catch (const std::exception& e) {
        throw_java(env, "java/lang/RuntimeException", e.what());
    }
    return decltype(fn())();
}

}  // namespace

extern "C" {

JNIEXPORT jlong JNICALL Java_io_github_shanikn_chordchart_Native_createAnalyzer(JNIEnv* env, jclass, jstring assets_dir,
                                                                               jint threads, jboolean xnnpack) {
    return guarded(env, [&]() -> jlong {
        AnalyzerConfig config;
        config.assets_dir = to_string(env, assets_dir);
        config.threads = threads;
        config.xnnpack = xnnpack;
        return reinterpret_cast<jlong>(new Analyzer(config));
    });
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_releaseAnalyzer(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<Analyzer*>(handle);
}

// One analysis's progress and cancel flag, shared between the core's threads (which write
// it) and the UI (which polls it: Native.sessionProgress). Polling keeps the JNI simple: the
// core's worker threads never call into Java.
struct Session {
    std::mutex lock;
    double stages[5] = {-1, -1, -1, -1, -1};  // beats, chords, key, bars, chart; -1 = not started
    std::atomic<bool> cancel{false};
};

int stage_index(const std::string& stage) {
    static const char* names[] = {"beats", "chords", "key", "bars", "chart"};
    for (int i = 0; i < 5; ++i)
        if (stage == names[i]) return i;
    return -1;
}

JNIEXPORT jlong JNICALL Java_io_github_shanikn_chordchart_Native_createSession(JNIEnv*, jclass) {
    return reinterpret_cast<jlong>(new Session);
}

JNIEXPORT jdoubleArray JNICALL Java_io_github_shanikn_chordchart_Native_sessionProgress(JNIEnv* env, jclass, jlong handle) {
    auto* session = reinterpret_cast<Session*>(handle);
    double copy[5];
    {
        std::lock_guard<std::mutex> guard(session->lock);
        std::memcpy(copy, session->stages, sizeof copy);
    }
    jdoubleArray out = env->NewDoubleArray(5);
    env->SetDoubleArrayRegion(out, 0, 5, copy);
    return out;
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_cancelSession(JNIEnv*, jclass, jlong handle) {
    reinterpret_cast<Session*>(handle)->cancel = true;
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_releaseSession(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<Session*>(handle);
}

JNIEXPORT jstring JNICALL Java_io_github_shanikn_chordchart_Native_analyze(JNIEnv* env, jclass, jlong handle, jshortArray pcm,
                                                                         jlong session_handle) {
    auto* session = reinterpret_cast<Session*>(session_handle);
    try {
        const jsize samples = env->GetArrayLength(pcm);
        std::unique_ptr<int16_t[]> copy(new int16_t[samples]);
        env->GetShortArrayRegion(pcm, 0, samples, reinterpret_cast<jshort*>(copy.get()));
        Progress progress;
        if (session) {
            progress = [session](const std::string& stage, double fraction) {
                const int i = stage_index(stage);
                if (i < 0) return;
                std::lock_guard<std::mutex> guard(session->lock);
                session->stages[i] = fraction;
            };
        }
        const Song song = reinterpret_cast<Analyzer*>(handle)->analyze(copy.get(), static_cast<size_t>(samples), progress,
                                                                        session ? &session->cancel : nullptr);
        return env->NewStringUTF(song.to_json().c_str());
    } catch (const Cancelled&) {
        throw_java(env, "java/util/concurrent/CancellationException", "cancelled");
    } catch (const AnalysisError& e) {
        throw_java(env, "io/github/shanikn/chordchart/AnalysisException", e.what());
    } catch (const std::exception& e) {
        throw_java(env, "java/lang/RuntimeException", e.what());
    }
    return nullptr;
}

JNIEXPORT jlong JNICALL Java_io_github_shanikn_chordchart_Native_createResampler(JNIEnv* env, jclass, jint rate, jint channels) {
    return guarded(env, [&]() -> jlong { return reinterpret_cast<jlong>(new MonoResampler(rate, channels)); });
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_feed(JNIEnv* env, jclass, jlong handle, jobject buffer, jint offset,
                                                                   jint bytes, jboolean is_float) {
    guarded(env, [&]() -> int {
        auto* base = static_cast<uint8_t*>(env->GetDirectBufferAddress(buffer));
        if (!base) throw std::runtime_error("not a direct buffer");
        auto* r = reinterpret_cast<MonoResampler*>(handle);
        if (is_float) {
            r->feed_float(reinterpret_cast<const float*>(base + offset), static_cast<size_t>(bytes) / sizeof(float));
        } else {
            r->feed(reinterpret_cast<const int16_t*>(base + offset), static_cast<size_t>(bytes) / sizeof(int16_t));
        }
        return 0;
    });
}

JNIEXPORT jshortArray JNICALL Java_io_github_shanikn_chordchart_Native_finish(JNIEnv* env, jclass, jlong handle) {
    return guarded(env, [&]() -> jshortArray {
        auto pcm = reinterpret_cast<MonoResampler*>(handle)->finish();
        jshortArray out = env->NewShortArray(static_cast<jsize>(pcm.size()));
        if (out) env->SetShortArrayRegion(out, 0, static_cast<jsize>(pcm.size()), reinterpret_cast<const jshort*>(pcm.data()));
        return out;
    });
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_releaseResampler(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<MonoResampler*>(handle);
}

// The whole file behind `fd` (from the app's ParcelFileDescriptor). Read through the
// descriptor itself: re-opening it by path (/proc/self/fd/N) fails for files from other apps
// or shared storage, which the app may read only through the descriptor it was handed.
static std::vector<uint8_t> read_fd(int fd) {
    std::vector<uint8_t> data;
    struct stat st {};
    if (fstat(fd, &st) == 0 && st.st_size > 0) data.reserve(static_cast<size_t>(st.st_size));
    uint8_t buffer[1 << 16];
    for (off_t offset = 0;;) {
        ssize_t n = pread(fd, buffer, sizeof buffer, offset);
        if (n < 0) throw std::runtime_error("cannot read the file");
        if (n == 0) break;
        data.insert(data.end(), buffer, buffer + n);
        offset += n;
    }
    return data;
}

// Whole-file in-process decoding (WebM/Ogg Opus): null if the core can't handle the file,
// so the caller falls back to MediaCodec. `listener` gets onProgress("decoding", fraction).
JNIEXPORT jshortArray JNICALL Java_io_github_shanikn_chordchart_Native_decodeFile(JNIEnv* env, jclass, jint fd, jobject listener) {
    jmethodID on_progress = nullptr;
    if (listener) on_progress = env->GetMethodID(env->GetObjectClass(listener), "onProgress", "(Ljava/lang/String;D)V");
    jstring stage = env->NewStringUTF("decoding");
    try {
        auto pcm = decode_file(read_fd(fd), [&](double f) {
            if (on_progress) env->CallVoidMethod(listener, on_progress, stage, f);
            if (env->ExceptionCheck()) throw Cancelled();  // the listener threw (e.g. cancelled): stop
        });
        jshortArray out = env->NewShortArray(static_cast<jsize>(pcm.size()));
        if (out) env->SetShortArrayRegion(out, 0, static_cast<jsize>(pcm.size()), reinterpret_cast<const jshort*>(pcm.data()));
        return out;
    } catch (const UnsupportedContainer&) {
        return nullptr;
    } catch (const Cancelled&) {
        return nullptr;  // the listener's exception is pending and reaches the caller
    } catch (const std::exception& e) {
        throw_java(env, "java/io/IOException", e.what());
        return nullptr;
    }
}

// In-process container reading (WebM/Ogg with Opus): 0 if the file isn't one of those,
// so the caller falls back to MediaExtractor.
JNIEXPORT jlong JNICALL Java_io_github_shanikn_chordchart_Native_openDemuxer(JNIEnv* env, jclass, jint fd) {
    try {
        return reinterpret_cast<jlong>(Demuxer::open(read_fd(fd)).release());
    } catch (const UnsupportedContainer&) {
        return 0;
    } catch (const std::exception& e) {
        throw_java(env, "java/io/IOException", e.what());
        return 0;
    }
}

// [sample rate, channels], and the mime type and csd buffers through the other calls
JNIEXPORT jintArray JNICALL Java_io_github_shanikn_chordchart_Native_demuxerFormat(JNIEnv* env, jclass, jlong handle) {
    const auto& t = reinterpret_cast<Demuxer*>(handle)->track();
    const jint values[5] = {t.sample_rate, t.channels, static_cast<jint>(t.csd.size()), t.encoder_delay, t.encoder_padding};
    jintArray out = env->NewIntArray(5);
    env->SetIntArrayRegion(out, 0, 5, values);
    return out;
}

JNIEXPORT jstring JNICALL Java_io_github_shanikn_chordchart_Native_demuxerMime(JNIEnv* env, jclass, jlong handle) {
    return env->NewStringUTF(reinterpret_cast<Demuxer*>(handle)->track().mime.c_str());
}

JNIEXPORT jbyteArray JNICALL Java_io_github_shanikn_chordchart_Native_demuxerCsd(JNIEnv* env, jclass, jlong handle, jint index) {
    const auto& csd = reinterpret_cast<Demuxer*>(handle)->track().csd.at(static_cast<size_t>(index));
    jbyteArray out = env->NewByteArray(static_cast<jsize>(csd.size()));
    env->SetByteArrayRegion(out, 0, static_cast<jsize>(csd.size()), reinterpret_cast<const jbyte*>(csd.data()));
    return out;
}

// Writes the next packet into `buffer` (a codec input buffer); returns its size, or -1 at
// the end. time_out[0] = presentation time (us), time_out[1] = bytes read, [2] = file size.
JNIEXPORT jint JNICALL Java_io_github_shanikn_chordchart_Native_demuxerNext(JNIEnv* env, jclass, jlong handle, jobject buffer,
                                                                          jlongArray time_out) {
    return guarded(env, [&]() -> jint {
        auto* demuxer = reinterpret_cast<Demuxer*>(handle);
        Packet packet;
        if (!demuxer->next(packet)) return -1;
        auto* dst = static_cast<uint8_t*>(env->GetDirectBufferAddress(buffer));
        const jlong capacity = env->GetDirectBufferCapacity(buffer);
        if (!dst || static_cast<jlong>(packet.data.size()) > capacity) throw std::runtime_error("packet larger than the input buffer");
        std::memcpy(dst, packet.data.data(), packet.data.size());
        const jlong info[3] = {packet.time_us, static_cast<jlong>(demuxer->position()), static_cast<jlong>(demuxer->size())};
        env->SetLongArrayRegion(time_out, 0, 3, info);
        return static_cast<jint>(packet.data.size());
    });
}

JNIEXPORT void JNICALL Java_io_github_shanikn_chordchart_Native_releaseDemuxer(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<Demuxer*>(handle);
}

}  // extern "C"
