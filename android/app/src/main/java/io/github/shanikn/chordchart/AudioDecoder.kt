package io.github.shanikn.chordchart

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import java.io.IOException

/**
 * Decodes an audio (or video) file with the phone's own decoders (MediaExtractor +
 * MediaCodec: MP3, AAC/M4A, WAV, Ogg/Opus/Vorbis, FLAC, WebM, ...) into what the analysis
 * takes: mono 16-bit PCM at 44.1 kHz. The mixing down and resampling happen in the core
 * (chordchart::MonoResampler), as the decoder's output arrives, so the song is held once.
 */
object AudioDecoder {
    const val SAMPLE_RATE = 44100

    class Unsupported(message: String) : IOException(message)

    /** Where the last decode spent its time (seconds), for the benchmark. */
    @Volatile var lastStats: Map<String, Any> = emptyMap()

    fun decode(context: Context, uri: Uri): ShortArray {
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(context, uri, null)
            return decode(extractor)
        } finally {
            extractor.release()
        }
    }

    fun decode(path: String): ShortArray {
        val extractor = MediaExtractor()
        // Open the file here and hand over the descriptor: setDataSource(path) makes the
        // separate media process open the path, which can't read app-private storage.
        java.io.FileInputStream(path).use { input ->
            try {
                extractor.setDataSource(input.fd)
                return decode(extractor)
            } finally {
                extractor.release()
            }
        }
    }

    private fun decode(extractor: MediaExtractor): ShortArray {
        val track = (0 until extractor.trackCount).firstOrNull {
            extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: throw Unsupported("no audio track in this file")
        extractor.selectTrack(track)
        val format = extractor.getTrackFormat(track)
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
        val started = System.nanoTime()
        try {
            val info = MediaCodec.BufferInfo()
            var inputDone = false
            while (true) {
                // Queue every input buffer that's free (no waiting), then wait for output only
                // when nothing could be queued. Waiting on each packet (a 6-minute Opus file
                // has ~18,000) made decoding take longer than the analysis.
                var queued = false
                while (!inputDone) {
                    val inIndex = codec.dequeueInputBuffer(0)
                    if (inIndex < 0) break
                    val buffer = codec.getInputBuffer(inIndex)!!
                    val size = extractor.readSampleData(buffer, 0)
                    if (size < 0) {
                        codec.queueInputBuffer(inIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputDone = true
                    } else {
                        codec.queueInputBuffer(inIndex, 0, size, extractor.sampleTime, 0)
                        extractor.advance()
                    }
                    queued = true
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
            lastStats = mapOf(
                "codec" to codec.name, "mime" to mime, "buffers" to buffers,
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
