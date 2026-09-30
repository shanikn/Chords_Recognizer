package io.github.shanikn.chordchart

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.IOException
import java.nio.ByteBuffer

/**
 * Decodes an audio (or video) file with the phone's own decoders (MediaCodec) into what the
 * analysis takes: mono 16-bit PCM at 44.1 kHz. The mixing down and resampling happen in the
 * core (chordchart::MonoResampler) as the decoder's output arrives.
 *
 * Packets come from the core's own container reader when it knows the format (WebM and Ogg
 * with Opus: YouTube audio), else from Android's MediaExtractor. MediaExtractor reads every
 * packet through a separate process (~3 ms per packet on the emulator: 19 s for a 2-minute
 * Opus song); the core reads them in-process. Both feed the same decoder, so the audio is the
 * same either way.
 */
object AudioDecoder {
    const val SAMPLE_RATE = 44100

    class Unsupported(message: String) : IOException(message)

    /** Where the last decode spent its time, for the benchmark. */
    @Volatile var lastStats: Map<String, Any> = emptyMap()

    /** Debug/benchmark only: always use MediaExtractor (to compare the two paths). */
    @Volatile var forceExtractor = false

    fun decode(context: Context, uri: Uri, progress: ((Double) -> Unit)? = null): ShortArray {
        val pfd = context.contentResolver.openFileDescriptor(uri, "r") ?: throw IOException("can't open $uri")
        pfd.use { return decode(pfd, progress) }
    }

    fun decode(path: String, progress: ((Double) -> Unit)? = null): ShortArray {
        ParcelFileDescriptor.open(java.io.File(path), ParcelFileDescriptor.MODE_READ_ONLY).use { return decode(it, progress) }
    }

    /** Debug/benchmark only: decode WebM/Ogg Opus with MediaCodec too (to compare). */
    @Volatile var forceMediaCodec = false

    private fun decode(pfd: ParcelFileDescriptor, progress: ((Double) -> Unit)?): ShortArray {
        // The core opens the same file through /proc/self/fd (in-process, no copy).
        val path = "/proc/self/fd/${pfd.fd}"
        if (!forceExtractor && !forceMediaCodec) {
            val started = System.nanoTime()
            val listener = progress?.let { p -> ProgressListener { _, f -> p(f) } }
            Native.decodeFile(path, listener)?.let { pcm ->
                lastStats = mapOf("codec" to "libopus (in-process)", "packets_from" to "in-process",
                    "total_s" to (System.nanoTime() - started) / 1e9)
                return pcm
            }
        }
        val native = if (forceExtractor) 0L else Native.openDemuxer(path)
        return if (native != 0L) {
            try {
                decode(NativeSource(native), progress)
            } finally {
                Native.releaseDemuxer(native)
            }
        } else {
            val extractor = MediaExtractor()
            try {
                extractor.setDataSource(pfd.fileDescriptor)
                decode(ExtractorSource(extractor), progress)
            } finally {
                extractor.release()
            }
        }
    }

    private interface PacketSource {
        val format: MediaFormat
        val name: String
        /** Fills `buffer` with the next packet; its size, or -1 at the end. */
        fun read(buffer: ByteBuffer): Int
        val timeUs: Long
        /** 0..1 */
        val fraction: Double
    }

    private class NativeSource(private val handle: Long) : PacketSource {
        private val info = LongArray(3)
        override val name = "in-process"
        override val format: MediaFormat = run {
            val (rate, channels, csdCount) = Native.demuxerFormat(handle)
            MediaFormat.createAudioFormat(Native.demuxerMime(handle), rate, channels).apply {
                for (i in 0 until csdCount) setByteBuffer("csd-$i", ByteBuffer.wrap(Native.demuxerCsd(handle, i)))
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 1 shl 16)
            }
        }
        override fun read(buffer: ByteBuffer): Int = Native.demuxerNext(handle, buffer, info)
        override val timeUs get() = info[0]
        override val fraction get() = if (info[2] > 0) info[1].toDouble() / info[2] else 0.0
    }

    private class ExtractorSource(private val extractor: MediaExtractor) : PacketSource {
        private val track = (0 until extractor.trackCount).firstOrNull {
            extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: throw Unsupported("no audio track in this file")
        override val name = "MediaExtractor"
        override val format: MediaFormat = extractor.getTrackFormat(track).also { extractor.selectTrack(track) }
        private val durationUs = if (format.containsKey(MediaFormat.KEY_DURATION)) format.getLong(MediaFormat.KEY_DURATION) else 0L
        override var timeUs = 0L
        override fun read(buffer: ByteBuffer): Int {
            val size = extractor.readSampleData(buffer, 0)
            if (size < 0) return -1
            timeUs = extractor.sampleTime
            extractor.advance()
            return size
        }
        override val fraction get() = if (durationUs > 0) (timeUs.toDouble() / durationUs).coerceIn(0.0, 1.0) else 0.0
    }

    private fun decode(source: PacketSource, progress: ((Double) -> Unit)?): ShortArray {
        val format = source.format
        val mime = format.getString(MediaFormat.KEY_MIME)!!
        val codec = try {
            MediaCodec.createDecoderByType(mime)
        } catch (e: Exception) {
            throw Unsupported("this phone can't decode $mime audio")
        }
        codec.configure(format, null, null, 0)
        codec.start()

        var resampler = 0L
        var isFloat = false
        var feedNanos = 0L
        var buffers = 0
        var lastReported = -1.0
        val started = System.nanoTime()
        try {
            val info = MediaCodec.BufferInfo()
            var inputDone = false
            while (true) {
                // Queue every input buffer that's free (no waiting); wait for output only when
                // nothing could be queued.
                var queued = false
                while (!inputDone) {
                    val inIndex = codec.dequeueInputBuffer(0)
                    if (inIndex < 0) break
                    val buffer = codec.getInputBuffer(inIndex)!!
                    val size = source.read(buffer)
                    if (size < 0) {
                        codec.queueInputBuffer(inIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputDone = true
                    } else {
                        codec.queueInputBuffer(inIndex, 0, size, source.timeUs, 0)
                    }
                    queued = true
                }
                if (progress != null && source.fraction - lastReported >= 0.01) {
                    lastReported = source.fraction
                    progress(lastReported)
                }
                val outIndex = codec.dequeueOutputBuffer(info, if (queued) 0 else 5_000)
                if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val out = codec.outputFormat
                    val encoding = if (out.containsKey(MediaFormat.KEY_PCM_ENCODING))
                        out.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                    isFloat = encoding == AudioFormat.ENCODING_PCM_FLOAT
                    if (resampler != 0L) Native.releaseResampler(resampler)
                    resampler = Native.createResampler(
                        out.getInteger(MediaFormat.KEY_SAMPLE_RATE),
                        out.getInteger(MediaFormat.KEY_CHANNEL_COUNT),
                    )
                } else if (outIndex >= 0) {
                    if (info.size > 0) {
                        if (resampler == 0L) {  // no format-changed event: use the track's format
                            resampler = Native.createResampler(
                                format.getInteger(MediaFormat.KEY_SAMPLE_RATE),
                                format.getInteger(MediaFormat.KEY_CHANNEL_COUNT),
                            )
                        }
                        val buffer = codec.getOutputBuffer(outIndex)!!
                        val t = System.nanoTime()
                        Native.feed(resampler, buffer, info.offset, info.size, isFloat)
                        feedNanos += System.nanoTime() - t
                        buffers++
                    }
                    codec.releaseOutputBuffer(outIndex, false)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
                }
            }
            if (resampler == 0L) throw Unsupported("the file has no audio samples")
            val t = System.nanoTime()
            val pcm = Native.finish(resampler)
            progress?.invoke(1.0)
            lastStats = mapOf(
                "codec" to codec.name, "mime" to mime, "packets_from" to source.name, "buffers" to buffers,
                "total_s" to (System.nanoTime() - started) / 1e9,
                "feed_s" to (feedNanos + System.nanoTime() - t) / 1e9,
            )
            return pcm
        } finally {
            if (resampler != 0L) Native.releaseResampler(resampler)
            codec.stop()
            codec.release()
        }
    }
}
