package io.github.shanikn.chordchart

import org.json.JSONObject

/** The analysis result: the fields of the core's Song JSON the app shows. */
data class Song(
    val duration: Double,
    val keyTonic: String,
    val keyMode: String,
    val bpm: Double,
    val meter: Int,
    val bars: List<Bar>,
    val warnings: List<String>,
    val timings: Map<String, Double>,
    /** seconds the analysis took */
    val elapsed: Double,
) {
    data class Bar(val index: Int, val start: Double, val end: Double, val chords: List<Chord>)

    /** @param beat 0-based beat in the bar where the chord starts */
    data class Chord(val beat: Int, val time: Double, val symbol: String)

    val keyName get() = "$keyTonic $keyMode"

    companion object {
        fun parse(json: String): Song = parse(JSONObject(json))

        fun parse(o: JSONObject): Song {
            val key = o.getJSONObject("key")
            val bars = o.getJSONArray("bars")
            val warnings = o.optJSONArray("warnings")
            val timings = o.optJSONObject("timings")
            return Song(
                duration = o.getDouble("duration"),
                keyTonic = key.getString("tonic"),
                keyMode = key.getString("mode"),
                bpm = o.getDouble("bpm"),
                meter = o.getInt("meter"),
                bars = (0 until bars.length()).map { i ->
                    val b = bars.getJSONObject(i)
                    val chords = b.getJSONArray("chords")
                    Bar(
                        index = b.getInt("index"),
                        start = b.getDouble("start"),
                        end = b.getDouble("end"),
                        chords = (0 until chords.length()).map { j ->
                            val c = chords.getJSONObject(j)
                            Chord(c.getInt("beat"), c.getDouble("time"), c.getString("symbol"))
                        },
                    )
                },
                warnings = warnings?.let { w -> (0 until w.length()).map { w.getString(it) } } ?: emptyList(),
                elapsed = o.optDouble("elapsed", 0.0),
                timings = timings?.keys()?.asSequence()?.associateWith { timings.getDouble(it) } ?: emptyMap(),
            )
        }
    }
}

/** "3:05" */
fun formatDuration(seconds: Double): String {
    val s = seconds.toInt()
    return "%d:%02d".format(s / 60, s % 60)
}
