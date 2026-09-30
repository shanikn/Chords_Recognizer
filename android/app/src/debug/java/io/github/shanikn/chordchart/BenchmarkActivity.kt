package io.github.shanikn.chordchart

import android.app.Activity
import android.os.Build
import android.os.Bundle
import android.util.Log
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/**
 * Debug builds only: analyse one file and write the result, timings and peak memory, for
 * android/tools/run_emulator.py (which starts it once per song, in a fresh process, so the
 * peak memory is per song):
 *
 *   adb shell am start -W -n io.github.shanikn.chordchart/.BenchmarkActivity \
 *       --es file /data/user/0/io.github.shanikn.chordchart/files/songs/x.webm --es out result.json \
 *       --ez xnnpack false --ei threads 4
 */
class BenchmarkActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val file = intent.getStringExtra("file") ?: return finish()
        // app-private storage: run_emulator.py copies songs in and reads results out with run-as
        val out = File(File(filesDir, "bench").apply { mkdirs() }, intent.getStringExtra("out") ?: "result.json")
        val xnnpack = intent.getBooleanExtra("xnnpack", false)
        val threads = intent.getIntExtra("threads", 4)
        thread(name = "benchmark") {
            val result = JSONObject()
                .put("file", file)
                .put("xnnpack", xnnpack)
                .put("threads", threads)
                .put("device", "${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}, ${Build.SUPPORTED_ABIS.first()}")
            try {
                if (intent.getBooleanExtra("extract_only", false)) {
                    // time MediaExtractor alone: every packet read, nothing decoded
                    val e = android.media.MediaExtractor()
                    java.io.FileInputStream(file).use { input -> e.setDataSource(input.fd) }
                    e.selectTrack(0)
                    val buffer = java.nio.ByteBuffer.allocateDirect(1 shl 20)
                    val s0 = System.nanoTime()
                    var packets = 0
                    while (e.readSampleData(buffer, 0) >= 0) { packets++; e.advance() }
                    result.put("extract_s", (System.nanoTime() - s0) / 1e9).put("packets", packets)
                    e.release()
                }
                AudioDecoder.forceExtractor = intent.getBooleanExtra("extractor", false)
                AudioDecoder.forceMediaCodec = intent.getBooleanExtra("mediacodec", false)
                // decode_only: the audio alone (no models loaded, no analysis)
                val decodeOnly = intent.getBooleanExtra("decode_only", false)
                val t0 = System.nanoTime()
                val analyzer = if (decodeOnly) null else ChordAnalyzer(this, threads, xnnpack)
                val t1 = System.nanoTime()
                val pcm = AudioDecoder.decode(file)
                val t2 = System.nanoTime()
                val song = analyzer?.analyze(pcm)
                val t3 = System.nanoTime()
                analyzer?.close()
                result.put("load_s", (t1 - t0) / 1e9)
                    .put("decode_s", (t2 - t1) / 1e9)
                    .put("analyze_s", (t3 - t2) / 1e9)
                    .put("samples", pcm.size)
                    .put("pcm_sha256", sha256(pcm))
                    .put("decoder", JSONObject(AudioDecoder.lastStats))
                if (song != null) result.put("song", JSONObject(song))
            } catch (e: Throwable) {
                Log.e("ChordChart", "benchmark failed", e)
                result.put("error", "${e.javaClass.simpleName}: ${e.message}")
            }
            result.put("memory", memory())
            out.writeText(result.toString(1))
            runOnUiThread { finish() }
        }
    }

    private fun sha256(pcm: ShortArray): String {
        val bytes = java.nio.ByteBuffer.allocate(pcm.size * 2).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        bytes.asShortBuffer().put(pcm)
        return java.security.MessageDigest.getInstance("SHA-256").digest(bytes.array()).joinToString("") { "%02x".format(it) }
    }

    /** Peak and current resident memory of this process (kB), from /proc/self/status. */
    private fun memory(): JSONObject {
        val memory = JSONObject()
        File("/proc/self/status").readLines().forEach { line ->
            val (key, value) = line.split(":", limit = 2).map { it.trim() } + listOf("", "")
            if (key == "VmHWM" || key == "VmRSS") memory.put(key, value.removeSuffix(" kB").trim().toLong())
        }
        return memory
    }
}
