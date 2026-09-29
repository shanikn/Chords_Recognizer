// JNI bridge between the Kotlin app and the platform-independent core. Kotlin side:
// io.github.shanikn.chordchart.Native.

#include <jni.h>

#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include "chordchart/analyzer.hpp"
#include "chordchart/audio.hpp"

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

JNIEXPORT jstring JNICALL Java_io_github_shanikn_chordchart_Native_analyze(JNIEnv* env, jclass, jlong handle, jshortArray pcm,
                                                                         jobject listener) {
    return guarded(env, [&]() -> jstring {
        const jsize samples = env->GetArrayLength(pcm);
        std::unique_ptr<int16_t[]> copy(new int16_t[samples]);
        env->GetShortArrayRegion(pcm, 0, samples, reinterpret_cast<jshort*>(copy.get()));
        jmethodID on_progress = nullptr;
        if (listener) {
            jclass cls = env->GetObjectClass(listener);
            on_progress = env->GetMethodID(cls, "onProgress", "(Ljava/lang/String;D)V");
        }
        // Progress arrives from the core's threads; only the calling thread may use `env`,
        // so worker-thread updates are dropped and the calling thread's are forwarded.
        JavaVM* vm = nullptr;
        env->GetJavaVM(&vm);
        const auto caller = std::this_thread::get_id();
        Progress progress = [&](const std::string& stage, double fraction) {
            if (!on_progress || std::this_thread::get_id() != caller) return;
            jstring s = env->NewStringUTF(stage.c_str());
            env->CallVoidMethod(listener, on_progress, s, fraction);
            env->DeleteLocalRef(s);
        };
        const Song song = reinterpret_cast<Analyzer*>(handle)->analyze(copy.get(), static_cast<size_t>(samples), progress);
        return env->NewStringUTF(song.to_json().c_str());
    });
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

}  // extern "C"
