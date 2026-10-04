package io.github.shanikn.chordchart

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlin.math.sqrt

/**
 * Records a song played out loud, through the microphone, as what the analysis takes: mono
 * 16-bit PCM at 44.1 kHz. Without the phone's voice processing where it allows that
 * (UNPROCESSED): noise suppression and automatic gain would flatten the music.
 *
 * Android silences the microphone for apps in the background, so while the user is in
 * another app (starting the song in Spotify, say) the recording is quiet; [stop] trims
 * quiet from both ends.
 */
class Recorder(context: Context) {
    /** Can't record: what the user can do about it. */
    class Failed(message: String) : Exception(message)

    private val unprocessed = (context.getSystemService(Context.AUDIO_SERVICE) as AudioManager)
        .getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
    private val chunks = ArrayList<ShortArray>()
    @Volatile private var running = false
    private var thread: Thread? = null
    @Volatile private var error: String? = null

    /** Samples recorded so far. */
    @Volatile var samples = 0L
        private set
    /** The latest loudness, 0..1, for a level meter. */
    @Volatile var level = 0f
        private set

    /** Needs the RECORD_AUDIO permission. */
    @SuppressLint("MissingPermission")
    fun start() {
        val minBuffer = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) throw Failed("This phone's microphone can't record at 44.1 kHz.")
        val source = if (unprocessed) MediaRecorder.AudioSource.UNPROCESSED else MediaRecorder.AudioSource.MIC
        val record = AudioRecord(source, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuffer, RATE / 2 * 2))
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw Failed("The microphone is busy or unavailable. Close any app that's recording and try again.")
        }
        running = true
        thread = Thread({
            try {
                record.startRecording()
                val buffer = ShortArray(RATE / 10)  // 100 ms
                while (running && samples < MAX_SAMPLES) {
                    val n = record.read(buffer, 0, buffer.size)
                    if (n < 0) {
                        error = "The microphone stopped working (error $n)."
                        break
                    }
                    if (n == 0) continue
                    synchronized(chunks) { chunks.add(buffer.copyOf(n)) }
                    samples += n
                    level = (rms(buffer, 0, n) / 8000.0).coerceAtMost(1.0).toFloat()
                }
            } finally {
                runCatching { record.stop() }
                record.release()
            }
        }, "Recorder").apply { start() }
    }

    /** True once it stopped by itself: the length limit, or a failure. */
    val finished get() = !running || thread?.isAlive == false

    /** Stops and returns the recording with quiet trimmed from both ends; Failed if nothing was heard. */
    fun stop(): ShortArray {
        running = false
        thread?.join()
        error?.let { throw Failed(it) }
        val all = ShortArray(synchronized(chunks) { chunks.sumOf { it.size } })
        var at = 0
        for (c in chunks) {
            c.copyInto(all, at)
            at += c.size
        }
        chunks.clear()
        val window = RATE / 2
        fun loud(start: Int) = rms(all, start, minOf(window, all.size - start)) >= QUIET
        var first = 0
        while (first < all.size && !loud(first)) first += window
        var end = all.size
        while (end > first && !loud(maxOf(first, end - window))) end -= window
        if (end - first < MIN_SAMPLES) {
            throw Failed("Chord Chart didn't hear a song. Play it out loud near the phone, at least ${MIN_SAMPLES / RATE} seconds of it.")
        }
        return all.copyOfRange(first, end)
    }

    /** Stops and throws the recording away. */
    fun cancel() {
        running = false
        thread?.join()
        chunks.clear()
    }

    companion object {
        const val RATE = AudioDecoder.SAMPLE_RATE
        const val MAX_SECONDS = 10 * 60
        private const val MAX_SAMPLES = MAX_SECONDS.toLong() * RATE
        private const val MIN_SAMPLES = 10 * RATE
        private const val QUIET = 60.0  // RMS of a half-second window, of 32768: about -55 dBFS

        private fun rms(pcm: ShortArray, start: Int, count: Int): Double {
            if (count <= 0) return 0.0
            var sum = 0.0
            for (i in start until start + count) sum += pcm[i].toDouble() * pcm[i]
            return sqrt(sum / count)
        }
    }
}
