package io.github.shanikn.chordchart

import java.nio.ByteBuffer

/** A problem with the audio itself (too short, no steady beat): shown to the user as is. */
class AnalysisException(message: String) : Exception(message)

fun interface ProgressListener {
    fun onProgress(stage: String, fraction: Double)
}

/** The native core (android/core, via app/src/main/cpp/jni.cpp). */
internal object Native {
    init {
        System.loadLibrary("chordchart")
    }

    @JvmStatic external fun createAnalyzer(assetsDir: String, threads: Int, xnnpack: Boolean): Long
    @JvmStatic external fun releaseAnalyzer(handle: Long)
    @JvmStatic external fun analyze(handle: Long, pcm: ShortArray, listener: ProgressListener?): String

    @JvmStatic external fun createResampler(sampleRate: Int, channels: Int): Long
    @JvmStatic external fun feed(handle: Long, buffer: ByteBuffer, offset: Int, bytes: Int, isFloat: Boolean)
    @JvmStatic external fun finish(handle: Long): ShortArray
    @JvmStatic external fun releaseResampler(handle: Long)
}
