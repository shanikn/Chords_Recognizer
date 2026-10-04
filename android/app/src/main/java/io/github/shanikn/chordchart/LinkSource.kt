package io.github.shanikn.chordchart

import android.net.Uri
import org.schabi.newpipe.extractor.MediaFormat
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.exceptions.AgeRestrictedContentException
import org.schabi.newpipe.extractor.exceptions.ContentNotAvailableException
import org.schabi.newpipe.extractor.exceptions.ExtractionException
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException
import org.schabi.newpipe.extractor.services.youtube.YoutubeParsingHelper
import org.schabi.newpipe.extractor.services.youtube.linkHandler.YoutubeSearchQueryHandlerFactory
import org.schabi.newpipe.extractor.stream.AudioStream
import org.schabi.newpipe.extractor.stream.AudioTrackType
import org.schabi.newpipe.extractor.stream.DeliveryMethod
import org.schabi.newpipe.extractor.stream.StreamInfoItem
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.abs

/**
 * A YouTube or Spotify link -> the song's audio in a local file, as the desktop app does it
 * (chordchart/sources.py and spotify.py), with NewPipe Extractor in place of yt-dlp.
 *
 * Spotify's own audio is never used (DRM, and its terms forbid it): the track's title, artists
 * and length come from its public page, then YouTube is searched for "<artist> - <title>" and
 * the result within ±3 s of the track's length is taken, an "<Artist> - Topic" channel first,
 * then the artist's own channel, then the closest length.
 */
object LinkSource {
    /** What the user can do about it, said plainly. */
    class LinkError(message: String) : IOException(message)

    sealed interface Link
    data class YouTube(val videoId: String) : Link {
        val url get() = "https://www.youtube.com/watch?v=$videoId"
    }
    data class Spotify(val trackId: String) : Link {
        val url get() = "https://open.spotify.com/track/$trackId"
    }

    data class Track(val title: String, val artists: List<String>, val duration: Double?) {
        val artist get() = artists.joinToString(", ")
    }

    data class Video(val id: String, val title: String, val channel: String, val duration: Double?)

    private const val TOLERANCE = 3.0  // seconds between Spotify's and the video's length
    private const val SEARCH_RESULTS = 5
    private const val MAX_DURATION = 20 * 60.0  // longer than this isn't a song
    private const val CHUNK = 1L shl 20  // YouTube throttles unranged downloads to real time
    private const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
    private const val SPOTIFY_HINT = "only single-track links are supported, like https://open.spotify.com/track/…"

    private val YOUTUBE_HOSTS = setOf("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")
    private val VIDEO_ID = Regex("^[A-Za-z0-9_-]{11}$")
    private val SPOTIFY_ID = Regex("^[A-Za-z0-9]{22}$")
    private val SPOTIFY_KINDS = mapOf(
        "album" to "an album", "playlist" to "a playlist", "artist" to "an artist",
        "show" to "a podcast", "episode" to "a podcast episode", "user" to "a profile",
        "collection" to "your library",
    )
    private val URL_IN_TEXT = Regex("""https?://\S+""")

    /** The first link in `text` (a shared message can be "Listen to … https://…"), or null. */
    fun findLink(text: String): String? =
        URL_IN_TEXT.find(text)?.value?.trimEnd('.', ',', ')', '"', '\'')
            ?: text.trim().takeIf { it.startsWith("spotify:") }

    /** A YouTube video or a Spotify track; LinkError, saying what's supported, for anything else. */
    fun parse(text: String): Link {
        val raw = findLink(text) ?: throw LinkError("That isn't a link. Paste a YouTube or Spotify link, like https://youtu.be/…")
        if (raw.startsWith("spotify:")) {
            val parts = raw.split(":")
            return spotify(parts.getOrNull(1).orEmpty(), parts.getOrNull(2).orEmpty(), raw)
        }
        val uri = Uri.parse(raw)
        val host = uri.host.orEmpty().lowercase()
        val path = uri.pathSegments.orEmpty()
        when (host) {
            in YOUTUBE_HOSTS -> {
                val id = when {
                    uri.path == "/watch" -> uri.getQueryParameter("v")
                    path.firstOrNull() in setOf<String?>("shorts", "live", "embed") -> path.getOrNull(1)
                    else -> null
                }
                if (id != null && VIDEO_ID.matches(id)) return YouTube(id)
                throw LinkError("This YouTube link isn't a single video. Open the video and share or copy its link.")
            }
            "youtu.be" -> path.firstOrNull()?.takeIf { VIDEO_ID.matches(it) }?.let { return YouTube(it) }
            "open.spotify.com", "play.spotify.com" -> {
                var parts = path
                if (parts.firstOrNull()?.startsWith("intl-") == true) parts = parts.drop(1)  // /intl-de/track/…
                if (parts.firstOrNull() == "embed") parts = parts.drop(1)
                return spotify(parts.getOrNull(0).orEmpty(), parts.getOrNull(1).orEmpty(), raw)
            }
            "spotify.link", "spotify.app.link" -> throw LinkError(
                "Spotify short links aren't supported: open it in a browser and copy the open.spotify.com/track/… address instead.",
            )
        }
        throw LinkError("Only YouTube and Spotify links are supported.")
    }

    private fun spotify(kind: String, id: String, raw: String): Link {
        if (kind == "track" && SPOTIFY_ID.matches(id)) return Spotify(id)
        SPOTIFY_KINDS[kind]?.let { throw LinkError("This Spotify link is $it; $SPOTIFY_HINT") }
        throw LinkError("Not a Spotify track link: $raw; $SPOTIFY_HINT")
    }

    // --- Spotify -> YouTube -----------------------------------------------------------

    private val META = Regex("""<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"""", RegexOption.IGNORE_CASE)

    /** Title, artists and length from the public track page's <meta> tags. */
    fun spotifyTrack(link: Spotify): Track {
        val page = try {
            httpText(link.url)
        } catch (e: HttpStatus) {
            if (e.code == 404) throw LinkError("Spotify doesn't know this track (check the link).")
            throw LinkError("Spotify answered ${e.code}. Try again later, or paste a YouTube link of the song.")
        }
        val meta = mutableMapOf<String, MutableList<String>>()
        for (m in META.findAll(page)) meta.getOrPut(m.groupValues[1]) { mutableListOf() }.add(unescape(m.groupValues[2]))
        val title = meta["og:title"]?.firstOrNull().orEmpty()
        var artists: List<String> = meta["music:musician_description"].orEmpty()
        if (artists.isEmpty()) {
            meta["og:description"]?.firstOrNull()?.takeIf { it.isNotEmpty() }?.let {
                artists = listOf(it.split(" · ")[0])  // "Artist · Album · Song · 2004"
            }
        }
        if (title.isEmpty()) {
            throw LinkError("Couldn't read this track's details from Spotify. Paste a YouTube link of the song instead.")
        }
        return Track(title, artists, meta["music:duration"]?.firstOrNull()?.toDoubleOrNull())
    }

    fun searchYouTube(query: String): List<Video> = youtube {
        val search = ServiceList.YouTube.getSearchExtractor(query, listOf(YoutubeSearchQueryHandlerFactory.VIDEOS), "")
        search.fetchPage()
        search.initialPage.items.filterIsInstance<StreamInfoItem>().take(SEARCH_RESULTS).mapNotNull { item ->
            val id = runCatching { ServiceList.YouTube.streamLHFactory.getId(item.url) }.getOrNull() ?: return@mapNotNull null
            Video(id, item.name.orEmpty(), item.uploaderName.orEmpty(), item.duration.takeIf { it > 0 }?.toDouble())
        }
    }

    private fun norm(text: String) = text.lowercase().replace(Regex("[^a-z0-9]+"), " ").trim()

    /** The best video for `track`, as desktop's spotify.choose(); null if none is close enough. */
    fun choose(track: Track, videos: List<Video>, tolerance: Double = TOLERANCE): Video? {
        val duration = track.duration ?: return null
        val artists = track.artists.filter { it.isNotEmpty() }.map(::norm).toSet()
        return videos.withIndex()
            .filter { (_, v) -> v.duration != null && abs(v.duration - duration) <= tolerance }
            .minWithOrNull(
                compareBy<IndexedValue<Video>>(
                    { (_, v) -> !(v.channel.endsWith(" - Topic") && norm(v.channel.removeSuffix(" - Topic")) in artists) },
                    { (_, v) -> norm(v.channel.removeSuffix(" - Topic")) !in artists },
                    { (_, v) -> abs(v.duration!! - duration) },
                    { it.index },
                ),
            )?.value
    }

    /** The YouTube video for a Spotify track; LinkError when no result is close enough. */
    fun match(track: Track): Video {
        val query = if (track.artist.isNotEmpty()) "${track.artist} - ${track.title}" else track.title
        val videos = searchYouTube(query)
        return choose(track, videos) ?: throw LinkError(
            "Couldn't find this exact recording on YouTube (no result within ${TOLERANCE.toInt()} s of its length). " +
                "Paste a YouTube link of the song instead.",
        )
    }

    // --- YouTube audio ----------------------------------------------------------------

    data class Audio(val title: String, val duration: Double, private val stream: AudioStream) {
        val suffix: String get() = stream.format?.suffix ?: "audio"
        internal val url: String get() = stream.content
    }

    /** The video's title and its best audio-only stream (Opus or AAC, the original language). */
    fun audio(link: YouTube): Audio = youtube {
        val extractor = ServiceList.YouTube.getStreamExtractor(link.url)
        extractor.fetchPage()
        val length = extractor.length.toDouble()
        if (length > MAX_DURATION) {
            throw LinkError("This video is ${length.toInt() / 60} minutes long; ChordChart takes songs up to ${(MAX_DURATION / 60).toInt()} minutes.")
        }
        val streams = extractor.audioStreams.filter {
            it.isUrl && it.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP &&
                (it.format == MediaFormat.WEBMA_OPUS || it.format == MediaFormat.M4A)
        }
        val original = streams.filter { it.audioTrackType == null || it.audioTrackType == AudioTrackType.ORIGINAL }
        val best = original.ifEmpty { streams }.maxByOrNull { it.averageBitrate.takeIf { b -> b > 0 } ?: it.bitrate }
            ?: throw LinkError("YouTube has no audio for this video that ChordChart can read.")
        Audio(extractor.name.orEmpty().ifEmpty { link.videoId }, length, best)
    }

    /** Downloads `audio` to `file` in 1 MB ranges, as YouTube's own players do. */
    fun download(audio: Audio, file: File, progress: (Double) -> Unit) {
        val total = Uri.parse(audio.url).getQueryParameter("clen")?.toLongOrNull() ?: -1L
        val tmp = File(file.path + ".part")
        try {
            tmp.outputStream().use { out ->
                var position = 0L
                val buffer = ByteArray(1 shl 16)
                while (total < 0 || position < total) {
                    val connection = open(audio.url + "&range=$position-${position + CHUNK - 1}")
                    var got = 0L
                    try {
                        val code = connection.responseCode
                        if (code !in 200..299) throw HttpStatus(code)
                        connection.inputStream.use { input ->
                            while (true) {
                                val n = input.read(buffer)
                                if (n < 0) break
                                out.write(buffer, 0, n)
                                got += n
                                progress(if (total > 0) ((position + got).toDouble() / total).coerceAtMost(1.0) else 0.0)
                            }
                        }
                    } finally {
                        connection.disconnect()
                    }
                    position += got
                    if (got < CHUNK) break  // the last range (or the server sent it all at once)
                }
                if (position == 0L) throw LinkError("YouTube sent no audio for this video.")
            }
            if (!tmp.renameTo(file)) throw IOException("couldn't save the download")
            progress(1.0)
        } catch (e: HttpStatus) {
            throw LinkError("YouTube refused the download (HTTP ${e.code}). Try again later.")
        } finally {
            tmp.delete()
        }
    }

    private fun open(url: String): HttpURLConnection {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 20_000
        connection.readTimeout = 30_000
        if (YoutubeParsingHelper.isVisionOsStreamingUrl(url)) {
            connection.setRequestProperty("User-Agent", YoutubeParsingHelper.getVisionOsUserAgent(null))
        } else {
            connection.setRequestProperty("User-Agent", USER_AGENT)
        }
        if (YoutubeParsingHelper.isWebStreamingUrl(url)) {
            connection.setRequestProperty("Origin", "https://www.youtube.com")
            connection.setRequestProperty("Referer", "https://www.youtube.com")
        }
        return connection
    }

    // --- network ----------------------------------------------------------------------

    private class HttpStatus(val code: Int) : IOException("HTTP $code")

    private fun httpText(url: String): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = 15_000
        connection.readTimeout = 20_000
        connection.setRequestProperty("User-Agent", "Mozilla/5.0")
        try {
            val code = connection.responseCode
            if (code !in 200..299) throw HttpStatus(code)
            return connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            connection.disconnect()
        }
    }

    private fun unescape(text: String): String =
        android.text.Html.fromHtml(text, android.text.Html.FROM_HTML_MODE_LEGACY).toString()

    @Volatile private var initialized = false

    /** Runs NewPipe Extractor code, its failures turned into what the user can do about them. */
    private fun <T> youtube(block: () -> T): T {
        if (!initialized) synchronized(this) {
            if (!initialized) {
                NewPipe.init(HttpDownloader)
                initialized = true
            }
        }
        try {
            return block()
        } catch (e: AgeRestrictedContentException) {
            throw LinkError("This video is age-restricted, so it can't be downloaded without signing in.")
        } catch (e: ContentNotAvailableException) {
            throw LinkError("This video isn't available: ${e.message ?: "it may be private or removed"}.")
        } catch (e: ReCaptchaException) {
            throw LinkError("YouTube is asking this phone to prove it isn't a robot. Try again later, or on another network.")
        } catch (e: ExtractionException) {
            throw LinkError("YouTube's page couldn't be read (${e.message ?: e.javaClass.simpleName}). An app update may be needed.")
        }
    }

    /** NewPipe Extractor's HTTP requests, through HttpURLConnection. */
    private object HttpDownloader : Downloader() {
        override fun execute(request: Request): Response {
            val connection = URL(request.url()).openConnection() as HttpURLConnection
            connection.connectTimeout = 20_000
            connection.readTimeout = 30_000
            connection.requestMethod = request.httpMethod()
            connection.setRequestProperty("User-Agent", USER_AGENT)
            for ((name, values) in request.headers()) {
                values.forEachIndexed { i, value ->
                    if (i == 0) connection.setRequestProperty(name, value) else connection.addRequestProperty(name, value)
                }
            }
            request.dataToSend()?.let { data ->
                connection.doOutput = true
                connection.outputStream.use { it.write(data) }
            }
            try {
                val code = connection.responseCode
                if (code == 429) throw ReCaptchaException("reCaptcha Challenge requested", request.url())
                val body = (if (code >= 400) connection.errorStream else connection.inputStream)?.use(InputStream::readBytes)
                val headers = connection.headerFields.filterKeys { it != null }
                return Response(code, connection.responseMessage, headers, body?.toString(Charsets.UTF_8), connection.url.toString())
            } finally {
                connection.disconnect()
            }
        }
    }
}
