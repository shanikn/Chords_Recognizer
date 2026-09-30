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

    /** Decodes WebM/Ogg Opus entirely in-process; null for other files. */
    @JvmStatic external fun decodeFile(path: String, listener: ProgressListener?): ShortArray?

    @JvmStatic external fun openDemuxer(path: String): Long
    @JvmStatic external fun demuxerFormat(handle: Long): IntArray
    @JvmStatic external fun demuxerMime(handle: Long): String
    @JvmStatic external fun demuxerCsd(handle: Long, index: Int): ByteArray
    @JvmStatic external fun demuxerNext(handle: Long, buffer: ByteBuffer, info: LongArray): Int
    @JvmStatic external fun releaseDemuxer(handle: Long)
}
