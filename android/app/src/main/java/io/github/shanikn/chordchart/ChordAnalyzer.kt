package io.github.shanikn.chordchart

import android.content.Context
import java.io.Closeable
import java.io.File

/**
 * The on-device analysis: load once (models and tables), analyze many songs. Returns the
 * Song as JSON, with the same fields as the desktop app's `Song.to_json()`.
 *
 * @param threads threads for the beat tracker (the heaviest model)
 * @param xnnpack run the beat tracker on ONNX Runtime's XNNPACK execution provider
 */
class ChordAnalyzer(context: Context, threads: Int = 4, xnnpack: Boolean = false) : Closeable {
    private var handle = Native.createAnalyzer(AnalysisAssets.prepare(context).path, threads, xnnpack)

    fun analyze(pcm: ShortArray, listener: ProgressListener? = null): String {
        check(handle != 0L) { "closed" }
        return Native.analyze(handle, pcm, listener)
    }

    override fun close() {
        if (handle != 0L) Native.releaseAnalyzer(handle)
        handle = 0L
    }
}

/**
 * The models and tables ship in the APK's assets (uncompressed); the core reads them as
 * files, so they're copied to the app's private storage once per app version.
 */
object AnalysisAssets {
    private const val DIR = "analysis"

    @Synchronized
    fun prepare(context: Context): File {
        val target = File(context.filesDir, DIR)
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        val stamp = File(target, ".version")
        val version = "${info.longVersionCode}-${info.lastUpdateTime}"
        if (stamp.isFile && stamp.readText() == version) return target
        target.deleteRecursively()
        target.mkdirs()
        for (name in context.assets.list(DIR).orEmpty()) {
            context.assets.open("$DIR/$name").use { input ->
                File(target, name).outputStream().use { input.copyTo(it) }
            }
        }
        stamp.writeText(version)
        return target
    }
}
