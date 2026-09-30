package io.github.shanikn.chordchart

import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

@Composable
fun ChordChartTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = colors) { Surface(Modifier.fillMaxSize(), content = content) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun App(
    screen: Screen,
    entries: List<History.Entry>,
    onPick: () -> Unit,
    model: AppViewModel,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (screen) {
                            is Screen.Chart -> screen.entry.title
                            is Screen.Analyzing -> screen.title
                            is Screen.Failed -> screen.title
                            Screen.About -> "About"
                            Screen.Home -> "ChordChart"
                        },
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    if (screen != Screen.Home && screen !is Screen.Analyzing) {
                        TextButton(onClick = model::home) { Text("‹ Back") }
                    }
                },
                actions = {
                    if (screen == Screen.Home) TextButton(onClick = model::about) { Text("About") }
                },
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (screen) {
                Screen.Home -> HomeScreen(entries, onPick, model::show, model::delete)
                is Screen.Analyzing -> AnalyzingScreen(screen, model::cancel)
                is Screen.Chart -> ChartScreen(screen.entry)
                is Screen.Failed -> FailedScreen(screen, onPick)
                Screen.About -> AboutScreen()
            }
        }
    }
    if (screen != Screen.Home) BackHandler { if (screen is Screen.Analyzing) model.cancel() else model.home() }
}

@Composable
private fun HomeScreen(
    entries: List<History.Entry>,
    onPick: () -> Unit,
    onOpen: (History.Entry) -> Unit,
    onDelete: (History.Entry) -> Unit,
) {
    var confirm by remember { mutableStateOf<History.Entry?>(null) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp)) {
        item {
            Text(
                "Chords, beats and bars of a song, worked out on this phone. Nothing is uploaded.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(16.dp))
            Button(onClick = onPick, modifier = Modifier.fillMaxWidth().height(56.dp)) {
                Text("Choose a song", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "Or share an audio or video file to ChordChart from another app.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(24.dp))
            if (entries.isNotEmpty()) Text("Recent", style = MaterialTheme.typography.titleMedium)
        }
        items(entries, key = { it.id }) { entry ->
            Row(
                Modifier.fillMaxWidth().clickable { onOpen(entry) }.padding(vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(entry.title, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val song = runCatching { entry.song }.getOrNull()
                    Text(
                        listOfNotNull(
                            song?.keyName,
                            song?.let { "${it.bpm.toInt()} BPM" },
                            song?.let { formatDuration(it.duration) },
                            DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date(entry.analyzedAt)),
                        ).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = { confirm = entry }) { Text("Delete") }
            }
            HorizontalDivider()
        }
    }
    confirm?.let { entry ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Delete this chart?") },
            text = { Text(entry.title) },
            confirmButton = { TextButton(onClick = { onDelete(entry); confirm = null }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Keep") } },
        )
    }
}

/** Rough share of the total time per stage (emulator timings), for the overall bar. */
private val STAGE_WEIGHT = mapOf(
    Stage.Decode to 0.10, Stage.Beats to 0.60, Stage.Chords to 0.20,
    Stage.Key to 0.04, Stage.Bars to 0.03, Stage.Chart to 0.03,
)

@Composable
private fun AnalyzingScreen(state: Screen.Analyzing, onCancel: () -> Unit) {
    // The screen stays on while the phone works: turning off would look like it stopped.
    val view = LocalView.current
    DisposableEffect(Unit) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = System.currentTimeMillis()
            delay(250)
        }
    }
    val overall = STAGE_WEIGHT.entries.sumOf { (stage, w) -> w * (state.progress[stage] ?: 0.0) }
    Column(Modifier.fillMaxSize().padding(24.dp)) {
        Text("Working it out…", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(16.dp))
        LinearProgressIndicator(progress = { overall.toFloat() }, modifier = Modifier.fillMaxWidth().height(8.dp))
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth()) {
            Text("${(overall * 100).toInt()}%", style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.weight(1f))
            Text(
                formatDuration((now - state.startedAt) / 1000.0),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(24.dp))
        for (stage in Stage.entries) {
            val p = state.progress[stage]
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(24.dp), contentAlignment = Alignment.Center) {
                    when {
                        p == null -> Text("○", color = MaterialTheme.colorScheme.outline)
                        p >= 1.0 -> Text("✓", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                        else -> CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    }
                }
                Spacer(Modifier.width(12.dp))
                Text(
                    stage.label,
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyLarge,
                    color = if (p == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                )
                if (p != null && p < 1.0) Text("${(p * 100).toInt()}%", style = MaterialTheme.typography.bodyMedium)
            }
        }
        Spacer(Modifier.weight(1f))
        OutlinedButton(onClick = onCancel, modifier = Modifier.fillMaxWidth()) { Text("Cancel") }
    }
}

/** How a chord is shown: "N" (no chord) as "N.C.". */
private fun display(symbol: String) = if (symbol == "N") "N.C." else symbol

@Composable
private fun ChartScreen(entry: History.Entry) {
    val song = remember(entry.id) { runCatching { entry.song }.getOrNull() }
    if (song == null) {
        Text("This chart can't be read.", Modifier.padding(24.dp))
        return
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Fact("Key", song.keyName)
            Fact("Tempo", "${song.bpm.toInt()} BPM")
            Fact("Meter", "${song.meter}/4")
            Fact("Length", formatDuration(song.duration))
        }
        for (warning in song.warnings) {
            Text(
                warning,
                Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        Spacer(Modifier.height(12.dp))
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            // ~4 bars a row on a phone held upright, more when it's turned or bigger
            val perRow = (maxWidth / 96.dp).toInt().coerceIn(2, 8)
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                for (row in song.bars.chunked(perRow)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for (bar in row) BarCell(bar, song.meter, Modifier.weight(1f))
                        repeat(perRow - row.size) { Spacer(Modifier.weight(1f)) }
                    }
                }
            }
        }
        Spacer(Modifier.height(16.dp))
        Text(
            "Analysed on this phone in ${"%.0f".format(song.elapsed)} s · ${entry.fileName}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun BarCell(bar: Song.Bar, meter: Int, modifier: Modifier) {
    Surface(
        modifier = modifier.height(52.dp),
        shape = MaterialTheme.shapes.small,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Box {
            Text(
                "${bar.index}",
                Modifier.padding(start = 3.dp, top = 1.dp),
                fontSize = 9.sp,
                color = MaterialTheme.colorScheme.outline,
            )
            // each chord gets room in proportion to the beats it lasts
            Row(Modifier.fillMaxSize().padding(start = 4.dp, end = 2.dp, top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                bar.chords.forEachIndexed { i, chord ->
                    val next = bar.chords.getOrNull(i + 1)?.beat ?: maxOf(meter, chord.beat + 1)
                    Text(
                        display(chord.symbol),
                        Modifier.weight((next - chord.beat).coerceAtLeast(1).toFloat()),
                        fontFamily = FontFamily.SansSerif,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = if (bar.chords.size > 2) 12.sp else 15.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Clip,
                        color = if (chord.symbol == "N") MaterialTheme.colorScheme.outline else MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}

@Composable
private fun FailedScreen(state: Screen.Failed, onPick: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp)) {
        Text("Couldn't make a chart", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))
        Text(state.message, style = MaterialTheme.typography.bodyLarge)
        Spacer(Modifier.height(24.dp))
        Button(onClick = onPick) { Text("Choose another song") }
    }
}

@Composable
private fun AboutScreen() {
    val context = LocalContext.current
    val notices = remember { context.assets.open("NOTICES.txt").bufferedReader().use { it.readText() } }
    val licenses = remember {
        context.assets.list("licenses").orEmpty().sorted().map { name ->
            name.removeSuffix(".txt") to context.assets.open("licenses/$name").bufferedReader().use { it.readText() }
        }
    }
    val version = remember { context.packageManager.getPackageInfo(context.packageName, 0).versionName }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Text("ChordChart $version", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(
            "Works fully offline: songs are analysed on the phone and never leave it. " +
                "Charts are kept on the phone until you delete them.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(16.dp))
        Text(notices, style = MaterialTheme.typography.bodySmall)
        for ((title, text) in licenses) {
            Spacer(Modifier.height(16.dp))
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(text.trim(), style = MaterialTheme.typography.bodySmall)
        }
    }
}
