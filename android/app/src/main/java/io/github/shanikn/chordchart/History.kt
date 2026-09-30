package io.github.shanikn.chordchart

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * Past analyses, in the app's private storage (nothing leaves the phone). Each is keyed by a
 * hash of the audio file's bytes, so opening the same song again shows its chart at once,
 * whatever it's called or wherever it came from.
 */
class History(context: Context) {
    private val dir = File(context.filesDir, "history").apply { mkdirs() }

    data class Entry(
        val id: String,
        val title: String,
        val fileName: String,
        val analyzedAt: Long,
        val songJson: String,
    ) {
        val song: Song by lazy { Song.parse(songJson) }
    }

    fun get(id: String): Entry? = read(File(dir, "$id.json"))

    fun all(): List<Entry> = dir.listFiles { f -> f.extension == "json" }.orEmpty()
        .mapNotNull { read(it) }
        .sortedByDescending { it.analyzedAt }

    fun save(entry: Entry) {
        val json = JSONObject()
            .put("id", entry.id)
            .put("title", entry.title)
            .put("file", entry.fileName)
            .put("analyzed_at", entry.analyzedAt)
            .put("song", JSONObject(entry.songJson))
        val tmp = File(dir, "${entry.id}.tmp")
        tmp.writeText(json.toString())
        tmp.renameTo(File(dir, "${entry.id}.json"))
    }

    fun delete(id: String) {
        File(dir, "$id.json").delete()
    }

    private fun read(file: File): Entry? = try {
        val o = JSONObject(file.readText())
        Entry(o.getString("id"), o.getString("title"), o.optString("file"), o.getLong("analyzed_at"), o.getJSONObject("song").toString())
    } catch (e: Exception) {
        null  // a damaged entry is skipped, not fatal
    }
}
