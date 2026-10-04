package io.github.shanikn.chordchart

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest

/** The stages shown while a song is analysed, in the order they finish. */
enum class Stage(val label: String) {
    Find("Finding it on YouTube"),
    Download("Downloading the audio"),
    Decode("Reading the audio"),
    Beats("Finding the beats"),
    Chords("Recognizing the chords"),
    Key("Detecting the key"),
    Bars("Finding the bars"),
    Chart("Building the chart"),
}

sealed interface Screen {
    data object Home : Screen
    data class Analyzing(
        val title: String,
        /** per stage shown, in order: null = not started, else 0..1 */
        val progress: Map<Stage, Double?>,
        val startedAt: Long,
    ) : Screen
    data class Chart(val entry: History.Entry, val fromHistory: Boolean) : Screen
    data class Failed(val title: String, val message: String) : Screen
    data object About : Screen
}

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val history = History(app)
    private val _screen = MutableStateFlow<Screen>(Screen.Home)
    val screen: StateFlow<Screen> = _screen
    private val _entries = MutableStateFlow(history.all())
    val entries: StateFlow<List<History.Entry>> = _entries

    // Loading the models takes a few seconds: start at once, in the background.
    private val analyzer = viewModelScope.async(Dispatchers.Default) { ChordAnalyzer(app) }
    private var job: Job? = null
    @Volatile private var session = 0L

    /** A song to analyse: what it's called, and how to get its audio once it's needed. */
    private class Source(val id: String, val title: String, val fileName: String, val decode: (progress: (Double) -> Unit) -> ShortArray)

    fun open(uri: Uri) {
        val (title, fileName) = names(uri)
        val app = getApplication<Application>()
        start(title, emptyList()) {
            val id = withContext(Dispatchers.IO) { hash(uri) }
            Source(id, title, fileName) { progress -> AudioDecoder.decode(app, uri, progress) }
        }
    }

    /** A YouTube or Spotify link, typed or shared from another app. */
    fun openLink(text: String) {
        val link = try {
            LinkSource.parse(text)
        } catch (e: LinkSource.LinkError) {
            _screen.value = Screen.Failed("Link", e.message!!)
            return
        }
        val folder = File(getApplication<Application>().cacheDir, "links").apply { mkdirs() }
        val stages = if (link is LinkSource.Spotify) listOf(Stage.Find, Stage.Download) else listOf(Stage.Download)
        start(if (link is LinkSource.Spotify) "Spotify song" else "YouTube video", stages) { r ->
            val video = when (link) {
                is LinkSource.YouTube -> link
                is LinkSource.Spotify -> {
                    r.progress(Stage.Find, 0.0)
                    val match = withContext(Dispatchers.IO) {
                        val track = LinkSource.spotifyTrack(link)
                        r.title(if (track.artist.isNotEmpty()) "${track.artist} - ${track.title}" else track.title)
                        LinkSource.match(track)
                    }
                    r.progress(Stage.Find, 1.0)
                    LinkSource.YouTube(match.id)
                }
            }
            // Keyed by the video: the same song from any link opens its chart without downloading.
            val id = "yt-${video.videoId}"
            history.get(id)?.let { return@start Source(id, it.title, it.fileName) { error("cached") } }
            val audio = withContext(Dispatchers.IO) { LinkSource.audio(video) }
            val title = if (link is LinkSource.Spotify) r.title else audio.title
            r.title(title)
            Source(id, title, video.url) { progress ->
                val file = File(folder, "${video.videoId}.${audio.suffix}")
                try {
                    r.progress(Stage.Download, 0.0)
                    LinkSource.download(audio, file) { r.progress(Stage.Download, it) }
                    AudioDecoder.decode(file.path, progress)
                } finally {
                    file.delete()
                }
            }
        }
    }

    /** Lets a source's preparation update what the analysing screen shows. */
    private interface Reporter {
        val title: String
        fun title(title: String)
        fun progress(stage: Stage, fraction: Double)
    }

    private fun start(initialTitle: String, before: List<Stage>, prepare: suspend CoroutineScope.(Reporter) -> Source) {
        job?.cancel()
        job = viewModelScope.launch(Dispatchers.Default) {
            var title = initialTitle
            val started = System.currentTimeMillis()
            val stages = before + Stage.entries.filter { it >= Stage.Decode }
            val progress = stages.associateWith<Stage, Double?> { null }.toMutableMap()
            fun show() {
                _screen.value = Screen.Analyzing(title, progress.toMap(), started)
            }
            val scope = this
            val getTitle = { title }
            val setTitle = { t: String -> title = t }
            val reporter = object : Reporter {
                override val title get() = getTitle()
                override fun title(title: String) = update { setTitle(title) }
                override fun progress(stage: Stage, fraction: Double) = update { progress[stage] = fraction }
                private fun update(change: () -> Unit) {
                    scope.ensureActive()
                    change()
                    show()
                }
            }
            show()
            try {
                val source = this.prepare(reporter)
                title = source.title
                history.get(source.id)?.let {
                    _screen.value = Screen.Chart(it, fromHistory = true)
                    return@launch
                }
                if (Stage.Download !in progress) {  // a link's download goes first
                    progress[Stage.Decode] = 0.0
                    show()
                }
                val pcm = withContext(Dispatchers.IO) {
                    source.decode { f ->
                        ensureActive()
                        progress[Stage.Decode] = f
                        show()
                    }
                }
                progress[Stage.Decode] = 1.0
                show()
                val analyzer = analyzer.await()
                val s = Native.createSession()
                session = s
                val poller = launch {
                    while (isActive) {
                        val p = Native.sessionProgress(s)
                        listOf(Stage.Beats, Stage.Chords, Stage.Key, Stage.Bars, Stage.Chart).forEachIndexed { i, stage ->
                            progress[stage] = p[i].takeIf { it >= 0 }
                        }
                        show()
                        delay(100)
                    }
                }
                val json = try {
                    analyzer.analyze(pcm, s)
                } finally {
                    poller.cancel()
                    session = 0L
                    Native.releaseSession(s)
                }
                val entry = History.Entry(source.id, title, source.fileName, System.currentTimeMillis(), json)
                history.save(entry)
                _entries.value = history.all()
                _screen.value = Screen.Chart(entry, fromHistory = false)
            } catch (e: CancellationException) {
                if (_screen.value is Screen.Analyzing) _screen.value = Screen.Home
            } catch (e: AnalysisException) {
                _screen.value = Screen.Failed(title, e.message ?: "the analysis failed")
            } catch (e: LinkSource.LinkError) {
                _screen.value = Screen.Failed(title, e.message!!)
            } catch (e: AudioDecoder.Unsupported) {
                _screen.value = Screen.Failed(title, e.message ?: "this file can't be read")
            } catch (e: SecurityException) {
                _screen.value = Screen.Failed(title, "Chord Chart wasn't allowed to open this file. Try \"Upload a song\" instead.")
            } catch (e: java.io.FileNotFoundException) {
                _screen.value = Screen.Failed(title, "The file isn't there any more.")
            } catch (e: java.net.UnknownHostException) {
                _screen.value = Screen.Failed(title, "No internet connection. Links need one; songs on the phone don't.")
            } catch (e: java.io.IOException) {
                _screen.value = Screen.Failed(title, "The download failed: ${e.message ?: e.javaClass.simpleName}. Check the internet connection and try again.")
            } catch (e: Exception) {
                _screen.value = Screen.Failed(title, "Something went wrong: ${e.message ?: e.javaClass.simpleName}")
            }
        }
    }

    fun cancel() {
        session.takeIf { it != 0L }?.let { Native.cancelSession(it) }
        job?.cancel()
        _screen.value = Screen.Home
    }

    fun show(entry: History.Entry) {
        _screen.value = Screen.Chart(entry, fromHistory = true)
    }

    fun delete(entry: History.Entry) {
        history.delete(entry.id)
        _entries.value = history.all()
        if ((_screen.value as? Screen.Chart)?.entry?.id == entry.id) _screen.value = Screen.Home
    }

    fun home() {
        _screen.value = Screen.Home
    }

    fun about() {
        _screen.value = Screen.About
    }

    /** (title without extension, file name) */
    private fun names(uri: Uri): Pair<String, String> {
        val resolver = getApplication<Application>().contentResolver
        val name = runCatching {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull() ?: uri.lastPathSegment ?: "Song"
        return name.substringBeforeLast('.').ifBlank { name } to name
    }

    private fun hash(uri: Uri): String {
        val digest = MessageDigest.getInstance("SHA-256")
        getApplication<Application>().contentResolver.openInputStream(uri)?.use { input ->
            val buffer = ByteArray(1 shl 16)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
            }
        } ?: throw AudioDecoder.Unsupported("can't open this file")
        return digest.digest().take(16).joinToString("") { "%02x".format(it) }
    }

    override fun onCleared() {
        if (analyzer.isCompleted && !analyzer.isCancelled) runCatching { analyzer.getCompleted().close() }
    }
}
