package io.github.shanikn.chordchart

import java.io.File
import java.io.IOException

/**
 * The Google Play build has no YouTube or Spotify links: Play doesn't allow apps that
 * download YouTube's audio, so NewPipe Extractor isn't in it (and the app has no internet
 * permission). The same declarations as the full build's LinkSource, so AppViewModel
 * compiles unchanged; the UI never offers a link (BuildConfig.LINKS is false).
 */
object LinkSource {
    class LinkError(message: String) : IOException(message)

    sealed interface Link
    data class YouTube(val videoId: String) : Link {
        val url get() = "https://www.youtube.com/watch?v=$videoId"
    }
    data class Spotify(val trackId: String) : Link

    data class Track(val title: String, val artists: List<String>, val duration: Double?) {
        val artist get() = artists.joinToString(", ")
    }

    data class Video(val id: String)

    data class Audio(val title: String, val suffix: String)

    private const val UNSUPPORTED = "This version of Chord Chart doesn't open links. Upload the song from your phone instead."

    fun parse(text: String): Link = throw LinkError(UNSUPPORTED)
    fun spotifyTrack(link: Spotify): Track = throw LinkError(UNSUPPORTED)
    fun match(track: Track): Video = throw LinkError(UNSUPPORTED)
    fun audio(link: YouTube): Audio = throw LinkError(UNSUPPORTED)
    fun download(audio: Audio, file: File, progress: (Double) -> Unit): Unit = throw LinkError(UNSUPPORTED)
}
